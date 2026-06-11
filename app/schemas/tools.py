"""Pydantic input models for the five agent tools — validated BEFORE any tool executes.

Mirrors contracts/agent_tools.md. The bounded agent acts ONLY through these five tools, and every tool
input is parsed/validated against the matching model here before the underlying service runs (FR-027);
invalid input is rejected, not executed. The cook's `ConstraintProfile` and `profile_id` are NOT in
these models — the loop supplies them from trusted context so the LLM can never widen constraints or
impersonate another cook. Bounds (string lengths, list sizes, k≤3) match the contract so the model
cannot request an oversized or out-of-range operation.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.recipe import Category

__all__ = [
    "SearchRecipesInput",
    "GetRecipeInput",
    "GetNutritionInput",
    "BuildShoppingListInput",
    "SubstituteIngredientInput",
]


class SearchRecipesInput(BaseModel):
    """`search_recipes`: semantic search for up to k wall-cleared cards, optionally within one category."""

    query: str = Field(min_length=1, max_length=200)
    category: Category | None = None
    k: int = Field(default=3, ge=1, le=3, description="Number of cards to return (display count, ≤3).")


class GetRecipeInput(BaseModel):
    """`get_recipe`: fetch one recipe's verbatim detail + scaled nutrition (wall governs visibility)."""

    recipe_id: uuid.UUID


class GetNutritionInput(BaseModel):
    """`get_nutrition`: scaled nutrition summary for one recipe at the cook's servings."""

    recipe_id: uuid.UUID


class BuildShoppingListInput(BaseModel):
    """`build_shopping_list`: consolidate ingredients across 1–14 plan recipes into one scaled list."""

    recipe_ids: list[uuid.UUID] = Field(min_length=1, max_length=14)


class SubstituteIngredientInput(BaseModel):
    """`substitute_ingredient`: curated, allergen-safe substitutes for one ingredient (never invented)."""

    ingredient: str = Field(min_length=1, max_length=60)
    recipe_id: uuid.UUID | None = None
