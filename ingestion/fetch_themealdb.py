"""Fetch + normalize TheMealDB food recipes into the common raw-recipe shape.

Offline ingestion stage 1 (food). Calls the `infra/external` TheMealDB adapter and flattens each meal
into the dict shape the rest of the pipeline consumes:

    {
        "source": "themealdb", "source_id": str, "title": str,
        "kind": "food",                 # routes categorize.py
        "source_category": str | None,  # TheMealDB strCategory (categorization hint)
        "cuisine": str | None, "image_url": str | None,
        "servings": int | None,         # TheMealDB does not provide servings → None
        "raw_ingredients": [str, ...],  # "<measure> <name>" lines for extract_ingredients
        "steps": [str, ...],            # split from strInstructions, stored verbatim
        "title_blob": str,              # title+instructions text, for keyword cues (drinks only; here unused)
    }
"""

from __future__ import annotations

from typing import Any

from app.infra.external.themealdb import TheMealDB

# TheMealDB packs up to 20 ingredient/measure pairs as strIngredient1..20 / strMeasure1..20.
_MAX_PAIRS = 20


def _ingredient_lines(meal: dict[str, Any]) -> list[str]:
    """Combine each non-empty strIngredientN/strMeasureN pair into a single raw line ("<measure> <name>")."""
    lines: list[str] = []
    for i in range(1, _MAX_PAIRS + 1):
        name = (meal.get(f"strIngredient{i}") or "").strip()
        measure = (meal.get(f"strMeasure{i}") or "").strip()
        if not name:
            continue
        lines.append(f"{measure} {name}".strip())
    return lines


def _steps(meal: dict[str, Any]) -> list[str]:
    """Split strInstructions into ordered, verbatim step lines (blank lines dropped, text unchanged)."""
    instructions = meal.get("strInstructions") or ""
    return [line.strip() for line in instructions.splitlines() if line.strip()]


def _normalize(meal: dict[str, Any]) -> dict[str, Any]:
    """Flatten one TheMealDB meal record into the common raw-recipe dict."""
    return {
        "source": "themealdb",
        "source_id": str(meal["idMeal"]),
        "title": (meal.get("strMeal") or "").strip(),
        "kind": "food",
        "source_category": (meal.get("strCategory") or None),
        "cuisine": (meal.get("strArea") or None),
        "image_url": (meal.get("strMealThumb") or None),
        "servings": None,
        "raw_ingredients": _ingredient_lines(meal),
        "steps": _steps(meal),
        "title_blob": " ".join(
            filter(None, [meal.get("strMeal"), meal.get("strCategory"), meal.get("strInstructions")])
        ),
    }


def fetch() -> list[dict[str, Any]]:
    """Pull the whole TheMealDB catalog and return it as normalized raw-recipe dicts."""
    with TheMealDB() as db:
        meals = db.iter_all_meals()
    return [_normalize(m) for m in meals]
