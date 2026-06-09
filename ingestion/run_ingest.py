"""Orchestrate the offline corpus build: fetch → categorize → extract → allergens + nutrition → load.

Runnable as `python -m ingestion.run_ingest` (wired to `make ingest`). Pulls every source, processes
each raw recipe deterministically into the stored shape, upserts it idempotently (so re-runs converge
without duplicates), then prints a coverage report. A failure on a single recipe is logged and skipped
so one bad row never aborts the whole run.
"""

from __future__ import annotations

from typing import Any

import structlog
from app.config import get_settings
from app.infra.db import Database
from app.infra.external.openfoodfacts import OpenFoodFacts

from ingestion import (
    allergens,
    categorize,
    coverage,
    extract_ingredients,
    fetch_kaggle,
    fetch_thecocktaildb,
    fetch_themealdb,
    load,
)
from ingestion import nutrition as nutrition_stage

log = structlog.get_logger()


def process_one(raw: dict[str, Any], off: OpenFoodFacts) -> dict[str, Any]:
    """Turn one normalized raw recipe into the fully-processed dict the loader stores.

    Runs the deterministic stages in order: pick a category, parse ingredients, tag allergens + derive
    diet flags (mutating the ingredient dicts), then derive nutrition — authoritative source data
    (Food.com) when present, else an Open Food Facts approximation. The result merges the raw passthrough
    fields with everything computed here.
    """
    category = categorize.categorize(raw)
    ingredients = extract_ingredients.extract(raw.get("raw_ingredients", []))
    diet_allergen = allergens.analyze(ingredients, off)
    servings = raw.get("servings") or 1
    # Prefer the source's own per-serving nutrition (Food.com) over approximating from OFF; returns None
    # when there is neither source data nor ingredients, leaving the recipe incomplete (never surfaced).
    nutrition = nutrition_stage.compute(raw, ingredients, off, basis_servings=servings)
    return {
        "source": raw["source"],
        "source_id": raw["source_id"],
        "title": raw["title"],
        "category": category.value,
        "cuisine": raw.get("cuisine"),
        "total_time_minutes": raw.get("total_time_minutes"),
        "servings": servings,
        "steps": raw.get("steps", []),
        "image_url": raw.get("image_url"),
        "ingredients": ingredients,
        "nutrition": nutrition,
        **diet_allergen,
    }


def _fetch_all() -> list[dict[str, Any]]:
    """Pull raw recipes from every source (Kaggle is optional and returns [] when no file is present)."""
    raws: list[dict[str, Any]] = []
    raws.extend(fetch_themealdb.fetch())
    raws.extend(fetch_thecocktaildb.fetch())
    raws.extend(fetch_kaggle.fetch())
    return raws


def run() -> None:
    """Run the full pipeline against the configured database and print the coverage report."""
    settings = get_settings()
    db = Database(settings.postgres_url)
    loaded = 0
    skipped = 0

    with OpenFoodFacts() as off:
        raws = _fetch_all()
        log.info("ingest.fetched", count=len(raws))
        session = db.session()
        try:
            for raw in raws:
                try:
                    processed = process_one(raw, off)
                    load.load_recipe(session, processed)
                    loaded += 1
                except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
                    skipped += 1
                    log.warning(
                        "ingest.recipe_failed",
                        source=raw.get("source"),
                        source_id=raw.get("source_id"),
                        error=str(exc),
                    )
            session.commit()
            report = coverage.compute(session)
            log.info("ingest.done", loaded=loaded, skipped=skipped)
            print(coverage.format_report(report))
        finally:
            session.close()
    db.dispose()


if __name__ == "__main__":
    run()
