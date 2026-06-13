"""Curated USDA fallback nutrition — average per-100g macros + per-item weights for common ingredients.

A safety net for the ingestion-time approximate aggregation (`ingestion/nutrition.aggregate`). Open Food
Facts is messy: it misses many bare ingredient names ("garlic", "egg") and count units ("2 cloves",
"1 egg") carry no mass, so those ingredients go *unmapped* and a recipe's totals can collapse to all
zeros — which the detail view honestly reports as "nutrition not available". This table closes that gap
WITHOUT fabricating anything: every number is an average from **USDA FoodData Central** (public-domain
reference data), consulted only as a fallback when OFF returns nothing. Because these are averages, any
recipe that relies on them stays flagged `is_approximate = true` (grounding, golden rule #2).

Three tables, all keyed on the normalized ingredient `name` the parser already produces (lowercased,
prep-words stripped) so lookups line up with the stored ingredient names:
  * `NUTRIMENTS_PER_100G` — per-100g energy + macros, using the SAME field keys as the OFF adapter
    (`energy-kcal_100g`, `proteins_100g`, `carbohydrates_100g`, `fat_100g`) so it drops straight into
    `aggregate` with no reshaping.
  * `ITEM_GRAMS` — average mass of ONE whole item (one egg, one onion, one garlic clove), for count /
    unit-less lines where volume/weight conversion doesn't apply.
  * `COUNT_UNIT_GRAMS` — generic average mass for count units whose size is stable across foods (a clove,
    a slice, a pinch). Deliberately omits genuinely variable units (can/package/stick/piece): guessing
    those risks gross errors, so they stay unmapped and the recipe reports honest partial coverage.

Offline only — the request path never calls this (or OFF); it reads the precomputed nutrition row.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "NUTRIMENTS_PER_100G",
    "ITEM_GRAMS",
    "COUNT_UNIT_GRAMS",
    "nutriments_per_100g",
    "item_grams",
    "count_unit_grams",
]

# Per-100g averages from USDA FoodData Central (rounded). Field keys match the OFF adapter's
# `_NUTRIMENT_FIELDS` so a fallback value is interchangeable with a real OFF nutriments payload.
# Tuple order authored as (kcal, protein g, carbs g, fat g) then expanded to the OFF field names below.
_RAW_PER_100G: dict[str, tuple[float, float, float, float]] = {
    # Vegetables / aromatics
    "onion": (40, 1.1, 9.3, 0.1),
    "garlic": (149, 6.4, 33.1, 0.5),
    "tomato": (18, 0.9, 3.9, 0.2),
    "potato": (77, 2.0, 17.5, 0.1),
    "sweet potato": (86, 1.6, 20.1, 0.1),
    "carrot": (41, 0.9, 9.6, 0.2),
    "bell pepper": (31, 1.0, 6.0, 0.3),
    "mushroom": (22, 3.1, 3.3, 0.3),
    "spinach": (23, 2.9, 3.6, 0.4),
    "broccoli": (34, 2.8, 6.6, 0.4),
    "celery": (16, 0.7, 3.0, 0.2),
    "cucumber": (15, 0.7, 3.6, 0.1),
    "lettuce": (15, 1.4, 2.9, 0.2),
    "cabbage": (25, 1.3, 5.8, 0.1),
    "zucchini": (17, 1.2, 3.1, 0.3),
    "corn": (86, 3.3, 19.0, 1.4),
    "peas": (81, 5.4, 14.5, 0.4),
    "green bean": (31, 1.8, 7.0, 0.2),
    "avocado": (160, 2.0, 8.5, 14.7),
    "ginger": (80, 1.8, 17.8, 0.8),
    # Fruit
    "banana": (89, 1.1, 22.8, 0.3),
    "apple": (52, 0.3, 13.8, 0.2),
    "lemon": (29, 1.1, 9.3, 0.3),
    "lime": (30, 0.7, 10.5, 0.2),
    "orange": (47, 0.9, 11.8, 0.1),
    "strawberry": (32, 0.7, 7.7, 0.3),
    # Proteins
    "chicken": (120, 22.5, 0.0, 2.6),
    "chicken breast": (120, 22.5, 0.0, 2.6),
    "beef": (254, 17.2, 0.0, 20.0),
    "ground beef": (254, 17.2, 0.0, 20.0),
    "pork": (242, 27.3, 0.0, 14.0),
    "bacon": (541, 37.0, 1.4, 42.0),
    "ham": (145, 20.9, 1.5, 5.5),
    "turkey": (189, 29.0, 0.0, 7.0),
    "salmon": (208, 20.4, 0.0, 13.4),
    "tuna": (130, 28.0, 0.0, 1.3),
    "shrimp": (99, 24.0, 0.2, 0.3),
    "egg": (143, 12.6, 0.7, 9.5),
    "tofu": (76, 8.1, 1.9, 4.8),
    # Dairy
    "milk": (61, 3.2, 4.8, 3.3),
    "butter": (717, 0.85, 0.06, 81.1),
    "cheese": (403, 24.9, 1.3, 33.1),
    "cheddar": (403, 24.9, 1.3, 33.1),
    "parmesan": (431, 38.5, 4.1, 29.0),
    "mozzarella": (300, 22.2, 2.2, 22.4),
    "cream": (340, 2.8, 2.8, 36.1),
    "heavy cream": (340, 2.8, 2.8, 36.1),
    "sour cream": (198, 2.4, 4.6, 19.4),
    "cream cheese": (342, 6.2, 4.1, 34.2),
    "yogurt": (61, 3.5, 4.7, 3.3),
    # Pantry / grains / oils
    "flour": (364, 10.3, 76.3, 1.0),
    "all-purpose flour": (364, 10.3, 76.3, 1.0),
    "bread": (265, 9.0, 49.0, 3.2),
    "rice": (365, 7.1, 80.0, 0.7),
    "pasta": (371, 13.0, 74.7, 1.5),
    "oats": (389, 16.9, 66.3, 6.9),
    "sugar": (387, 0.0, 100.0, 0.0),
    "brown sugar": (380, 0.1, 98.1, 0.0),
    "salt": (0, 0.0, 0.0, 0.0),
    "pepper": (251, 10.4, 64.0, 3.3),
    "black pepper": (251, 10.4, 64.0, 3.3),
    "cinnamon": (247, 4.0, 80.6, 1.2),
    "basil": (23, 3.2, 2.7, 0.6),
    "parsley": (36, 3.0, 6.3, 0.8),
    "cilantro": (23, 2.1, 3.7, 0.5),
    "olive oil": (884, 0.0, 0.0, 100.0),
    "vegetable oil": (884, 0.0, 0.0, 100.0),
    "oil": (884, 0.0, 0.0, 100.0),
    "cornstarch": (381, 0.3, 91.3, 0.05),
    "honey": (304, 0.3, 82.4, 0.0),
    "maple syrup": (260, 0.0, 67.0, 0.1),
    "soy sauce": (53, 8.1, 4.9, 0.6),
    "vinegar": (18, 0.0, 0.9, 0.0),
    "vanilla extract": (288, 0.1, 12.7, 0.1),
    "cocoa": (228, 19.6, 57.9, 13.7),
    "chocolate": (546, 4.9, 61.2, 31.3),
    "peanut butter": (588, 25.1, 20.0, 50.4),
    "coconut milk": (230, 2.3, 5.5, 23.8),
    "tomato sauce": (24, 1.2, 5.3, 0.3),
    "tomato paste": (82, 4.3, 18.9, 0.5),
    "water": (0, 0.0, 0.0, 0.0),
}

# Expand the compact tuples into OFF-shaped payloads so callers can use a fallback exactly like a real
# OFF nutriments dict (same keys, same per-100g basis).
NUTRIMENTS_PER_100G: dict[str, dict[str, float]] = {
    name: {
        "energy-kcal_100g": kcal,
        "proteins_100g": protein,
        "carbohydrates_100g": carbs,
        "fat_100g": fat,
    }
    for name, (kcal, protein, carbs, fat) in _RAW_PER_100G.items()
}

# Average mass (grams) of ONE whole item, USDA reference weights. Used for count / unit-less lines where
# the ingredient itself implies the portion size (one egg, one medium onion, one garlic clove).
ITEM_GRAMS: dict[str, float] = {
    "egg": 50.0,
    "onion": 110.0,
    "garlic": 3.0,  # one clove
    "tomato": 123.0,
    "potato": 173.0,
    "sweet potato": 130.0,
    "carrot": 61.0,
    "bell pepper": 119.0,
    "mushroom": 18.0,
    "celery": 40.0,  # one stalk
    "cucumber": 201.0,
    "zucchini": 196.0,
    "avocado": 150.0,
    "shallot": 25.0,
    "jalapeno": 14.0,
    "green onion": 15.0,
    "scallion": 15.0,
    "banana": 118.0,
    "apple": 182.0,
    "lemon": 58.0,
    "lime": 67.0,
    "orange": 131.0,
    "strawberry": 12.0,
}

# Generic average mass (grams) per single count unit, for units whose size is reasonably stable across
# foods. Variable-mass units (can/package/stick/piece/handful-of-anything) are intentionally excluded —
# guessing them risks large errors, so those ingredients stay unmapped and coverage is reported honestly.
COUNT_UNIT_GRAMS: dict[str, float] = {
    "clove": 3.0,
    "cloves": 3.0,
    "slice": 25.0,
    "slices": 25.0,
    "pinch": 0.36,
    "pinches": 0.36,
    "dash": 0.6,
    "dashes": 0.6,
    "sprig": 2.0,
    "sprigs": 2.0,
}


def _normalize(name: str) -> str:
    """Lowercase and collapse whitespace so lookups are robust to casing/padding (mirrors the parser)."""
    return " ".join(name.lower().split())


def _lookup(table: dict[str, Any], name: str) -> Any | None:
    """Exact-then-singular/plural lookup against a name-keyed table; None when the name isn't curated.

    The parser may keep a trailing 's' ("eggs", "onions"), so after an exact miss we try stripping or
    adding a trailing 's' — the same naive fallback the substitution table uses — before giving up.
    """
    key = _normalize(name)
    if key in table:
        return table[key]
    if key.endswith("s") and key[:-1] in table:
        return table[key[:-1]]
    if f"{key}s" in table:
        return table[f"{key}s"]
    return None


def nutriments_per_100g(name: str) -> dict[str, float] | None:
    """Return curated USDA per-100g nutriments for an ingredient name, or None when it isn't covered."""
    return _lookup(NUTRIMENTS_PER_100G, name)


def item_grams(name: str) -> float | None:
    """Return the average mass (g) of one whole item of this ingredient, or None when unknown."""
    return _lookup(ITEM_GRAMS, name)


def count_unit_grams(unit: str) -> float | None:
    """Return the generic average mass (g) for one of a stable count unit, or None when not modeled."""
    return COUNT_UNIT_GRAMS.get(unit.lower())
