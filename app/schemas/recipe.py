"""Pydantic response models for the cook-facing recipe surface (cards + detail + nutrition).

These mirror contracts/recipes.openapi.yaml. The domain enums (Category/Allergen) are reused from
app.models.recipe so there is a single source of truth for their string values. DTOs are produced ONLY
by services/shared/recipe_view.py (the wall choke point) — never built directly in a router.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.recipe import Allergen, Category

__all__ = [
    "Category",
    "Allergen",
    "NutritionSummary",
    "RecipeCard",
    "IngredientView",
    "RecipeDetail",
]


class NutritionSummary(BaseModel):
    """Nutrition totals scaled to the cook's servings; `is_approximate` flags partial coverage."""

    servings: int = Field(description="Servings these values are scaled to (the cook's default).")
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    is_approximate: bool = Field(
        description="True when some ingredients could not be mapped to nutrition."
    )
    unmapped_ingredient_count: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of the recipe's ingredients could not be measured into the totals. The cook-facing "
            "view subtracts this from the ingredient count to show honest partial coverage "
            "('estimated from N of M ingredients')."
        ),
    )


class RecipeCard(BaseModel):
    """A browse/list card: just enough to show and click through, with up-to-four key ingredients."""

    id: str
    title: str
    category: Category
    key_ingredients: list[str]
    image_url: str | None = None


class IngredientView(BaseModel):
    """One parsed ingredient line on the detail view; raw_text preserves the original source line."""

    name: str
    quantity: float | None = None
    unit: str | None = None
    raw_text: str


class RecipeDetail(BaseModel):
    """The full recipe view: verbatim steps, parsed ingredients, scaled nutrition, allergen tags."""

    id: str
    title: str
    category: Category
    cuisine: str | None = None
    total_time_minutes: int | None = None
    servings: int
    steps: list[str] = Field(description="Stored steps rendered verbatim — never rewritten.")
    ingredients: list[IngredientView]
    nutrition: NutritionSummary
    allergens: list[str]
    is_favorite: bool
