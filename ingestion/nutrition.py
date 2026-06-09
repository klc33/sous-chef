"""Derive a per-recipe nutrition row at ingestion, preferring authoritative source data over estimates.

Two strategies, picked by `compute`:
  * **Authoritative** — when the source ships its own nutrition (Food.com's per-serving `nutrition`
    column), convert it directly. These totals are exact, so `is_approximate = false`.
  * **Approximate** — otherwise, map each ingredient's quantity+unit to grams, pull OFF per-100g
    nutriments (cached), scale, and sum across the recipe. Any ingredient lacking an OFF mapping or a
    usable quantity is counted as unmapped and `is_approximate` is set True — partial coverage is
    signalled honestly, never fabricated (research §6).

The runtime NEVER calls OFF; it only reads + scales this precomputed row.
"""

from __future__ import annotations

from typing import Any

from app.infra.external.openfoodfacts import OpenFoodFacts

# --- Food.com authoritative nutrition --------------------------------------------------------------
# Food.com's `nutrition` column is a 7-element list of PER-SERVING values, in this fixed order:
# [calories (kcal), total fat (PDV), sugar (PDV), sodium (PDV), protein (PDV), saturated fat (PDV),
#  carbohydrates (PDV)] — where PDV is "percentage of daily value". Only the indices we store are named.
_FC_CALORIES = 0
_FC_TOTAL_FAT_PDV = 1
_FC_PROTEIN_PDV = 4
_FC_CARBS_PDV = 6

# FDA reference Daily Values (in grams) that Food.com's PDV percentages are taken against (the pre-2016
# values the dataset was built on). Grams = PDV/100 * DV. Only fat/protein/carbs are needed here.
_DV_TOTAL_FAT_G = 65.0
_DV_PROTEIN_G = 50.0
_DV_CARBS_G = 300.0


def from_food_com(values: list[float]) -> dict[str, Any]:
    """Convert a Food.com per-serving `nutrition` list into a nutrition_cache-shaped dict (exact).

    Calories are already absolute kcal; the macro entries are percentages of a daily value, so each is
    converted back to grams via its reference DV. Because the source states these per serving, the basis
    is 1 serving and the totals are authoritative — `is_approximate` is False and nothing is unmapped.
    The caller is responsible for having validated `values` (7 non-negative numbers).
    """
    return {
        "basis_servings": 1,
        "calories": round(float(values[_FC_CALORIES]), 2),
        "protein_g": round(float(values[_FC_PROTEIN_PDV]) / 100.0 * _DV_PROTEIN_G, 2),
        "carbs_g": round(float(values[_FC_CARBS_PDV]) / 100.0 * _DV_CARBS_G, 2),
        "fat_g": round(float(values[_FC_TOTAL_FAT_PDV]) / 100.0 * _DV_TOTAL_FAT_G, 2),
        "is_approximate": False,
        "unmapped_ingredient_count": 0,
    }


def compute(
    raw: dict[str, Any],
    ingredients: list[dict[str, Any]],
    off: OpenFoodFacts,
    *,
    basis_servings: int,
) -> dict[str, Any] | None:
    """Pick the best nutrition for a recipe: authoritative source data over OFF approximation.

    Uses the recipe's own `food_com_nutrition` (per-serving, exact) when the fetch stage parsed one;
    otherwise aggregates Open Food Facts per-ingredient nutriments (approximate) when there are
    ingredients to sum. With neither source data nor ingredients there is nothing to compute, so returns
    None and the recipe is left incomplete (and thus never surfaced).
    """
    food_com = raw.get("food_com_nutrition")
    if food_com is not None:
        return from_food_com(food_com)
    if ingredients:
        return aggregate(ingredients, off, basis_servings=basis_servings)
    return None


# --- Open Food Facts approximate aggregation -------------------------------------------------------

# Unit → grams conversion. Volume units use an approximate ~1 g/ml density (good enough for an
# "approximate" flag-bearing estimate). Count-like units (clove/slice/piece) have no reliable mass and
# are intentionally absent, so those ingredients count as unmapped.
_UNIT_TO_GRAMS: dict[str, float] = {
    "g": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "mg": 0.001,
    "oz": 28.35, "ounce": 28.35, "ounces": 28.35,
    "lb": 453.6, "lbs": 453.6, "pound": 453.6, "pounds": 453.6,
    "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0,
    "l": 1000.0, "liter": 1000.0, "liters": 1000.0, "litre": 1000.0, "litres": 1000.0,
    "tsp": 5.0, "teaspoon": 5.0, "teaspoons": 5.0,
    "tbsp": 15.0, "tablespoon": 15.0, "tablespoons": 15.0,
    "cup": 240.0, "cups": 240.0,
    "pint": 473.0, "pints": 473.0,
    "quart": 946.0, "quarts": 946.0,
}


def _grams(ingredient: dict[str, Any]) -> float | None:
    """Convert an ingredient's parsed quantity+unit to grams, or None when it cannot be determined."""
    quantity = ingredient.get("quantity")
    unit = ingredient.get("unit")
    if quantity is None or unit is None:
        return None
    factor = _UNIT_TO_GRAMS.get(unit)
    if factor is None:
        return None
    return float(quantity) * factor


def aggregate(
    ingredients: list[dict[str, Any]], off: OpenFoodFacts, *, basis_servings: int
) -> dict[str, Any]:
    """Sum scaled OFF nutriments across ingredients into a nutrition_cache-shaped dict.

    Returns calories + protein/carbs/fat totals for `basis_servings`, plus `is_approximate` and
    `unmapped_ingredient_count`. An ingredient is "unmapped" when its grams can't be determined or OFF
    has no per-100g nutriments for it; any unmapped ingredient makes the whole total approximate.
    """
    calories = protein = carbs = fat = 0.0
    unmapped = 0

    for ing in ingredients:
        grams = _grams(ing)
        nutriments = off.lookup_ingredient(ing["name"]).get("nutriments", {})
        if grams is None or not nutriments:
            unmapped += 1
            continue
        scale = grams / 100.0  # OFF nutriments are per 100 g.
        calories += float(nutriments.get("energy-kcal_100g", 0.0)) * scale
        protein += float(nutriments.get("proteins_100g", 0.0)) * scale
        carbs += float(nutriments.get("carbohydrates_100g", 0.0)) * scale
        fat += float(nutriments.get("fat_100g", 0.0)) * scale

    return {
        "basis_servings": basis_servings,
        "calories": round(calories, 2),
        "protein_g": round(protein, 2),
        "carbs_g": round(carbs, 2),
        "fat_g": round(fat, 2),
        "is_approximate": unmapped > 0,
        "unmapped_ingredient_count": unmapped,
    }
