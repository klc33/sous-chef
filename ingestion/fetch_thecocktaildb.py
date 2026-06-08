"""Fetch + normalize TheCocktailDB non-alcoholic drinks into the common raw-recipe shape.

Offline ingestion stage 1 (drinks). Produces the same dict shape as fetch_themealdb, with `kind="drink"`
and a `glass` hint plus a `title_blob` (title + instructions + glass) so categorize.py can decide hot
vs cold drink by keyword cues.
"""

from __future__ import annotations

from typing import Any

from app.infra.external.thecocktaildb import TheCocktailDB

# TheCocktailDB packs up to 15 ingredient/measure pairs.
_MAX_PAIRS = 15


def _ingredient_lines(drink: dict[str, Any]) -> list[str]:
    """Combine each non-empty strIngredientN/strMeasureN pair into a single raw line ("<measure> <name>")."""
    lines: list[str] = []
    for i in range(1, _MAX_PAIRS + 1):
        name = (drink.get(f"strIngredient{i}") or "").strip()
        measure = (drink.get(f"strMeasure{i}") or "").strip()
        if not name:
            continue
        lines.append(f"{measure} {name}".strip())
    return lines


def _steps(drink: dict[str, Any]) -> list[str]:
    """Split strInstructions into ordered, verbatim step lines (blank lines dropped)."""
    instructions = drink.get("strInstructions") or ""
    return [line.strip() for line in instructions.splitlines() if line.strip()]


def _normalize(drink: dict[str, Any]) -> dict[str, Any]:
    """Flatten one TheCocktailDB drink record into the common raw-recipe dict."""
    return {
        "source": "thecocktaildb",
        "source_id": str(drink["idDrink"]),
        "title": (drink.get("strDrink") or "").strip(),
        "kind": "drink",
        "source_category": (drink.get("strCategory") or None),
        "cuisine": None,
        "image_url": (drink.get("strDrinkThumb") or None),
        "servings": None,
        "glass": (drink.get("strGlass") or None),
        "raw_ingredients": _ingredient_lines(drink),
        "steps": _steps(drink),
        "title_blob": " ".join(
            filter(
                None,
                [drink.get("strDrink"), drink.get("strInstructions"), drink.get("strGlass")],
            )
        ),
    }


def fetch() -> list[dict[str, Any]]:
    """Pull all non-alcoholic drinks and return them as normalized raw-recipe dicts."""
    with TheCocktailDB() as db:
        drinks = db.iter_non_alcoholic_drinks()
    return [_normalize(d) for d in drinks]
