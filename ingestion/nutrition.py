"""Derive a per-recipe nutrition row at ingestion, preferring authoritative source data over estimates.

Two strategies, picked by `compute`:
  * **Authoritative** — when the source ships its own nutrition (Food.com's per-serving `nutrition`
    column), convert it directly. These totals are exact, so `is_approximate = false`.
  * **Approximate** — otherwise, map each ingredient's quantity+unit to grams, pull per-100g nutriments
    (OFF cached, falling back to curated USDA averages when OFF can't match), scale, and sum across the
    recipe. Count units and bare names that OFF misses are recovered from `ingredient_nutrition_data`
    where possible; anything still unresolved is counted as unmapped. This branch is always
    `is_approximate = True` — partial coverage is signalled honestly, never fabricated (research §6).

The runtime NEVER calls OFF; it only reads + scales this precomputed row.
"""

from __future__ import annotations

from typing import Any

from app.infra.external.openfoodfacts import OpenFoodFacts

from ingestion.ingredient_nutrition_data import (
    count_unit_grams,
    item_grams,
    nutriments_per_100g,
)

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
    """Convert an ingredient's parsed quantity+unit to grams, or None when it cannot be determined.

    Tries three resolutions, most specific first: a known weight/volume unit (`_UNIT_TO_GRAMS`), then —
    for count or unit-less lines that those tables can't handle — an ingredient-specific average item
    mass ("one egg", "one onion"), and finally a generic per-count-unit mass ("a clove", "a slice").
    The latter two only kick in where the original logic returned None, so this strictly *widens*
    coverage; a still-unresolved line returns None and is counted unmapped exactly as before.
    """
    quantity = ingredient.get("quantity")
    if quantity is None:
        return None
    unit = ingredient.get("unit")

    # 1) Known weight/volume unit — the exact path (unchanged).
    if unit is not None:
        factor = _UNIT_TO_GRAMS.get(unit)
        if factor is not None:
            return float(quantity) * factor

    # 2) Count / unit-less line: average mass of one whole item of this ingredient.
    per_item = item_grams(ingredient["name"])
    # 3) Fall back to a generic per-count-unit mass when the ingredient itself isn't a known whole item.
    if per_item is None and unit is not None:
        per_item = count_unit_grams(unit)
    if per_item is not None:
        return float(quantity) * per_item

    return None


def aggregate(
    ingredients: list[dict[str, Any]], off: OpenFoodFacts, *, basis_servings: int
) -> dict[str, Any]:
    """Sum scaled OFF nutriments across ingredients into a nutrition_cache-shaped dict.

    Returns calories + protein/carbs/fat totals for `basis_servings`, plus `is_approximate` and
    `unmapped_ingredient_count`. This whole branch is an estimate by construction — it sums per-100g
    OFF/USDA averages over ingredient-to-grams conversions — so `is_approximate` is ALWAYS True here
    (only the authoritative `from_food_com` path is exact). An ingredient is "unmapped" when its grams
    can't be determined or neither OFF nor the USDA fallback has per-100g nutriments for it;
    `unmapped_ingredient_count` reports that partial coverage separately from the approximate flag.
    """
    calories = protein = carbs = fat = 0.0
    unmapped = 0

    for ing in ingredients:
        grams = _grams(ing)
        nutriments = off.lookup_ingredient(ing["name"]).get("nutriments", {})
        # When OFF has nothing for this name, fall back to the curated USDA per-100g averages so a
        # common ingredient OFF simply can't match no longer counts as unmapped (still approximate).
        if not nutriments:
            nutriments = nutriments_per_100g(ing["name"]) or {}
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
        "is_approximate": True,  # aggregation is an estimate even at full coverage (averages, not exact)
        "unmapped_ingredient_count": unmapped,
    }
