"""Assign each raw recipe exactly one of the five fixed categories — deterministically, at ingestion.

Category is a committed lookup, never a runtime guess (research.md §2, SC-005):
  * drinks → hot_drink vs cold_drink by keyword cues in the title/instructions/glass blob,
  * food   → breakfast / lunch / dinner via a curated source-category lookup,
  * ambiguous food → a single documented default (lunch) so "exactly one category" always holds.

The function returns one of the Category enum *values* (strings) so it drops straight into the recipe row.
"""

from __future__ import annotations

from typing import Any

from app.models.recipe import Category

# Keyword cues that mark a drink as "hot"; anything else defaults to cold.
_HOT_DRINK_CUES = (
    "hot",
    "warm",
    "coffee",
    "tea",
    "mulled",
    "toddy",
    "cocoa",
    "chocolate",
    "espresso",
    "latte",
    "cappuccino",
    "steamed",
)

# Curated map from TheMealDB strCategory → one of the three food categories.
_FOOD_CATEGORY_LOOKUP = {
    "breakfast": Category.BREAKFAST,
    "starter": Category.LUNCH,
    "side": Category.LUNCH,
    "dessert": Category.LUNCH,
    "vegetarian": Category.LUNCH,
    "vegan": Category.LUNCH,
    "miscellaneous": Category.LUNCH,
    "pasta": Category.DINNER,
    "beef": Category.DINNER,
    "chicken": Category.DINNER,
    "lamb": Category.DINNER,
    "pork": Category.DINNER,
    "goat": Category.DINNER,
    "seafood": Category.DINNER,
}

# The documented fallback when a food recipe's category cannot be resolved (research §2).
_FOOD_DEFAULT = Category.LUNCH

# Title keywords that pull an otherwise-unmapped food recipe toward breakfast.
_BREAKFAST_CUES = ("breakfast", "pancake", "omelette", "omelet", "porridge", "granola", "cereal")


def _categorize_drink(recipe: dict[str, Any]) -> Category:
    """Pick hot_drink vs cold_drink from keyword cues in the recipe's title/instructions/glass blob."""
    blob = (recipe.get("title_blob") or recipe.get("title") or "").lower()
    if any(cue in blob for cue in _HOT_DRINK_CUES):
        return Category.HOT_DRINK
    return Category.COLD_DRINK


def _categorize_food(recipe: dict[str, Any]) -> Category:
    """Map a food recipe to breakfast/lunch/dinner via the curated lookup, then keywords, then default."""
    source_category = (recipe.get("source_category") or "").strip().lower()
    if source_category in _FOOD_CATEGORY_LOOKUP:
        return _FOOD_CATEGORY_LOOKUP[source_category]

    # No usable source category (e.g. Kaggle): try a breakfast keyword cue before defaulting.
    blob = (recipe.get("title_blob") or recipe.get("title") or "").lower()
    if any(cue in blob for cue in _BREAKFAST_CUES):
        return Category.BREAKFAST
    return _FOOD_DEFAULT


def categorize(recipe: dict[str, Any]) -> Category:
    """Return the single Category for a raw recipe, routing on its `kind` (drink vs food)."""
    if recipe.get("kind") == "drink":
        return _categorize_drink(recipe)
    return _categorize_food(recipe)
