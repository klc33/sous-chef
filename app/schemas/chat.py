"""Pydantic request/response models for POST /chat — the single conversational turn endpoint.

Mirrors contracts/chat.openapi.yaml. `ChatRequest` is the cook's free text (+ optional explicit
category); `ChatResponse` is the one response shape for every turn — a grounded reply plus, depending on
intent, ranked recipe cards, a meal plan, a shopping list, or a substitution result. The `RecipeCard`
returned here is the SAME wall-cleared DTO from schemas/recipe.py (cards are only ever built by the
recipe_view choke point), so a chat turn can never emit a recipe the wall would withhold. `refused`
marks a safe guardrail refusal (still a 200 with a safe reply, never unsafe content).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.recipe import Category
from app.schemas.recipe import RecipeCard

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "MealPlanDay",
    "MealPlan",
    "ShoppingLine",
    "ShoppingList",
    "SubstitutionResult",
]


class ChatRequest(BaseModel):
    """One turn's input: the cook's message and an optional explicit category hint."""

    message: str = Field(min_length=1, description="The cook's free-text request.")
    category: Category | None = Field(
        default=None, description="Optional explicit category; otherwise inferred from the message."
    )


class MealPlanDay(BaseModel):
    """One day of a meal plan: a 1-based day number and the (wall-cleared) recipe chosen for it."""

    day: int = Field(ge=1)
    recipe: RecipeCard


class MealPlan(BaseModel):
    """A multi-day plan maximizing distinct KNOWN cuisines; `shortfall_note` set when variety/length fell short."""

    days: list[MealPlanDay]
    distinct_cuisines: int = Field(
        description="Count of distinct KNOWN cuisines (>=3 when the corpus allows; 'unknown' never counts)."
    )
    shortfall_note: str | None = Field(
        default=None, description="Set when the requested length or >=3 distinct cuisines could not be met."
    )


class ShoppingLine(BaseModel):
    """One consolidated ingredient line: merged quantity/unit when compatible, plus its source recipes."""

    ingredient: str
    quantity: float | None = None
    unit: str | None = None
    from_recipes: list[str] = Field(description="Titles/ids of the plan recipes this line aggregates.")


class ShoppingList(BaseModel):
    """Exactly one consolidated, deduplicated, serving-scaled list for a whole meal plan."""

    lines: list[ShoppingLine]


class SubstitutionResult(BaseModel):
    """A curated, allergen-safe substitution answer; `none_safe` when nothing curated is safe."""

    ingredient: str
    substitutes: list[str] = Field(
        description="Curated, allergen-safe replacements (never invented). Empty when none_safe=true."
    )
    none_safe: bool = Field(
        description="True when no curated substitute is safe for the cook's declared allergens."
    )


class ChatResponse(BaseModel):
    """The single response shape for every turn (normal answer OR safe refusal).

    `reply` is always present and grounded (never invents recipes/steps). The optional payloads are
    populated per intent: `recipes` for find_recipe, `meal_plan` + `shopping_list` for plan_meals,
    `substitution` for substitution. `refused=true` marks a guardrail refusal carrying only a safe reply.
    """

    reply: str = Field(description="Natural-language reply (grounded; never invents recipes/steps).")
    intent: str = Field(description="The classified intent for this turn.")
    refused: bool = Field(
        default=False, description="True when an input/output rail refused the turn (no unsafe content)."
    )
    recipes: list[RecipeCard] = Field(
        default_factory=list, description="Up to 3 ranked, wall-cleared cards for find_recipe turns."
    )
    meal_plan: MealPlan | None = None
    shopping_list: ShoppingList | None = None
    substitution: SubstitutionResult | None = None
