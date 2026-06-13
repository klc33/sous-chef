"""Recompute cached nutrition for the existing corpus using the current ingestion logic.

A one-off, idempotent maintenance pass: the curated USDA fallback + count-unit gram weights
(`ingestion/ingredient_nutrition_data.py`) widened nutrition coverage, but already-ingested rows were
computed under the old logic — many collapsed to all-zeros ("nutrition not available"). This re-runs the
approximate aggregation over each recipe's STORED ingredients (reading the on-disk OFF cache, so no live
calls) and replaces its nutrition row in place.

Safe by construction:
  * Only `nutrition_cache` rows change — every other recipe field is left untouched.
  * Authoritative rows (`is_approximate = false`, e.g. Food.com source nutrition) are SKIPPED so this
    can never downgrade exact data to an estimate. The aggregation path is always approximate, matching
    what these rows already are.
  * Recompute is purely additive vs. the old logic, so a recipe's coverage can only improve or stay equal.

Run on the host (full dev env + current code), pointing at the mapped Postgres port, e.g. (PowerShell):
    $env:POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
    uv run python -m scripts.backfill_nutrition
"""

from __future__ import annotations

import os

from app.infra.external.openfoodfacts import OpenFoodFacts
from app.repo import recipes as recipe_repo
from ingestion import nutrition as nutrition_stage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _is_all_zero(nutrition: object) -> bool:
    """True when a nutrition row carries no macros (the 'not available' case we most want to fix)."""
    if nutrition is None:
        return False
    return all(
        float(getattr(nutrition, field)) == 0.0
        for field in ("calories", "protein_g", "carbs_g", "fat_g")
    )


def run() -> None:
    """Recompute + replace approximate nutrition rows for the whole corpus and print a before/after report."""
    url = os.environ.get(
        "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
    )
    engine = create_engine(url)

    updated = 0
    skipped_authoritative = 0
    fixed_zeros = 0  # rows that were all-zero before and now carry macros

    with OpenFoodFacts() as off, Session(engine) as session:
        recipes = recipe_repo.iter_with_nutrition(session)
        before_zeros = sum(1 for r in recipes if _is_all_zero(r.nutrition))

        for recipe in recipes:
            existing = recipe.nutrition
            # Leave authoritative (exact) rows alone — recomputing would downgrade them to an estimate.
            if existing is None or not existing.is_approximate:
                skipped_authoritative += 1
                continue

            was_zero = _is_all_zero(existing)
            ingredients = [
                {"name": ing.name, "quantity": ing.quantity, "unit": ing.unit}
                for ing in recipe.ingredients
            ]
            recomputed = nutrition_stage.aggregate(
                ingredients, off, basis_servings=existing.basis_servings
            )
            recipe_repo.set_nutrition(session, recipe, recomputed)
            updated += 1
            if was_zero and not all(
                recomputed[f] == 0.0 for f in ("calories", "protein_g", "carbs_g", "fat_g")
            ):
                fixed_zeros += 1

        session.commit()

        after = recipe_repo.iter_with_nutrition(session)
        after_zeros = sum(1 for r in after if _is_all_zero(r.nutrition))

    print(
        "nutrition backfill complete:\n"
        f"  recipes scanned        : {len(recipes)}\n"
        f"  rows recomputed        : {updated}\n"
        f"  authoritative skipped  : {skipped_authoritative}\n"
        f"  all-zero before        : {before_zeros}\n"
        f"  all-zero after         : {after_zeros}\n"
        f"  newly fixed (zero->data): {fixed_zeros}"
    )

    engine.dispose()


if __name__ == "__main__":
    run()
