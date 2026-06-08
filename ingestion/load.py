"""Compute completeness and upsert a fully-processed recipe — the ingestion write step.

Takes a "processed recipe" dict (after categorize + extract + allergens + nutrition), computes
`is_complete` (the surfacing gate), and writes it idempotently through `app.repo.recipes.upsert_recipe`
so the recipe + ingredients + nutrition land in one transaction keyed on (source, source_id). All DB
access stays in the repo layer — this module never touches the session beyond passing it through.
"""

from __future__ import annotations

from typing import Any

from app.repo import recipes as recipes_repo
from sqlalchemy.orm import Session


def compute_is_complete(processed: dict[str, Any]) -> bool:
    """Return True only when a recipe has everything needed to be surfaced (FR-020).

    Requires: a category, at least one parsed ingredient, at least one stored step, and a computed
    nutrition row. Allergen analysis always runs (producing a possibly-empty list + certainty flag), so
    it is implicitly present. An incomplete recipe is still stored but will never pass the browse filter.
    """
    return bool(
        processed.get("category")
        and processed.get("ingredients")
        and processed.get("steps")
        and processed.get("nutrition") is not None
    )


def load_recipe(session: Session, processed: dict[str, Any]) -> None:
    """Compute is_complete and upsert one processed recipe (idempotent on its source key).

    `servings` falls back to 1 when the source did not state it (the nutrition basis). Delegates the
    actual write to the repo so this stays a thin orchestration step.
    """
    servings = processed.get("servings") or 1
    is_complete = compute_is_complete(processed)
    recipes_repo.upsert_recipe(
        session,
        source=processed["source"],
        source_id=processed["source_id"],
        title=processed["title"],
        category=str(processed["category"]),
        cuisine=processed.get("cuisine"),
        total_time_minutes=processed.get("total_time_minutes"),
        servings=servings,
        steps=processed["steps"],
        image_url=processed.get("image_url"),
        allergens=processed["allergens"],
        allergen_certain=processed["allergen_certain"],
        is_vegetarian=processed["is_vegetarian"],
        is_vegan=processed["is_vegan"],
        is_pescatarian=processed["is_pescatarian"],
        is_complete=is_complete,
        ingredients=processed["ingredients"],
        nutrition=processed.get("nutrition"),
    )
