"""The SINGLE choke point that turns recipe rows into cook-facing DTOs — always behind the wall.

Every cook-facing recipe response (cards, detail, favorites) is built here, and both functions REQUIRE
a `ConstraintProfile` and run `constraint_guard` before constructing any DTO. That is the architectural
guarantee behind golden rule #1: a new endpoint cannot return a recipe without calling this module, so
it inherits the wall for free. The regression test (T039) enumerates every path and fails if one skips
this choke point.

Grounding (golden rule #2): cards/detail are assembled only from stored row fields — steps are rendered
verbatim, never rewritten.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.recipe import Recipe
from app.schemas.recipe import IngredientView, NutritionSummary, RecipeCard, RecipeDetail
from app.services.user import constraint_guard
from app.services.user.constraint_guard import ConstraintProfile

# Cards surface at most this many key ingredients (FR-011): the first few in stored order.
_MAX_KEY_INGREDIENTS = 4


def _key_ingredients(recipe: Recipe) -> list[str]:
    """Return the recipe's first up-to-four ingredient names in stored order (the card preview)."""
    return [ing.name for ing in recipe.ingredients[:_MAX_KEY_INGREDIENTS]]


def to_cards(recipes: Iterable[Recipe], cp: ConstraintProfile) -> list[RecipeCard]:
    """Filter recipes through the wall, then build a RecipeCard for each survivor (order preserved).

    The guard runs FIRST: only non-violating recipes ever become a card, so an empty result is honest
    (no relaxation, no substitution).
    """
    safe = constraint_guard.filter(recipes, cp)
    return [
        RecipeCard(
            id=str(recipe.id),
            title=recipe.title,
            category=recipe.category,
            key_ingredients=_key_ingredients(recipe),
            image_url=recipe.image_url,
        )
        for recipe in safe
    ]


def to_detail(
    recipe: Recipe,
    cp: ConstraintProfile,
    *,
    is_favorite: bool,
    nutrition: NutritionSummary,
) -> RecipeDetail | None:
    """Build a RecipeDetail for one recipe, or None when the wall withholds it.

    Runs the guard first: if the recipe violates the cook's constraints, returns None so the caller can
    answer 404 (the wall must not be bypassable on the detail path, and existence must not leak). When
    it passes, assembles the detail from stored fields — steps verbatim — plus the caller-supplied
    scaled nutrition and favorite flag.
    """
    if constraint_guard.violates(recipe, cp):
        return None

    return RecipeDetail(
        id=str(recipe.id),
        title=recipe.title,
        category=recipe.category,
        cuisine=recipe.cuisine,
        total_time_minutes=recipe.total_time_minutes,
        servings=recipe.servings,
        steps=list(recipe.steps),
        ingredients=[
            IngredientView(
                name=ing.name,
                quantity=float(ing.quantity) if ing.quantity is not None else None,
                unit=ing.unit,
                raw_text=ing.raw_text,
            )
            for ing in recipe.ingredients
        ],
        nutrition=nutrition,
        allergens=list(recipe.allergens),
        is_favorite=is_favorite,
    )
