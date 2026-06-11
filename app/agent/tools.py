"""The agent's tools — the ONLY way the bounded loop can act (contracts/agent_tools.md).

The LLM never touches the DB, the wall, or the cook's identity directly: it can only emit a call to one
of the functions registered here, and every call goes through `dispatch`, which (1) validates the raw
arguments against the matching Pydantic model in `app/schemas/tools.py` BEFORE anything runs — invalid
input is rejected, not executed (FR-027) — and (2) runs the handler with the cook's resolved
`ConstraintProfile` + `profile_id` supplied from trusted loop context, never from the model. So the model
can neither widen the cook's constraints nor impersonate another cook.

Every recipe-bearing result is produced through `services/shared/recipe_view` / `rag` (the wall choke
point) or is explicitly filtered against `constraint_guard`, so no tool can surface or aggregate a recipe
the wall would withhold (FR-028). Surfaced rows are collected on the `ToolContext` so the meal-plan
service can assemble its plan from real recipes without re-querying.

This registers all five tools (search / get_recipe / get_nutrition / build_shopping_list /
substitute_ingredient) so the agent can also swap an allergen-unsafe ingredient within a plan (US4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.recipe import Recipe
from app.repo import favorites as repo_favorites
from app.repo import recipes as repo_recipes
from app.schemas.tools import (
    BuildShoppingListInput,
    GetNutritionInput,
    GetRecipeInput,
    SearchRecipesInput,
    SubstituteIngredientInput,
)
from app.services.shared import recipe_view
from app.services.user import constraint_guard, rag, shopping_list, substitution
from app.services.user import nutrition as nutrition_service
from app.services.user.constraint_guard import ConstraintProfile


@dataclass
class ToolContext:
    """Trusted per-turn context every tool runs with — the cook's identity/constraints + a result sink.

    `cp`, `profile_id`, and `servings` come from the request (never the LLM), so the model can't widen
    constraints, impersonate a cook, or change the serving scale. `surfaced` collects the wall-cleared
    recipe rows the tools returned (keyed by id) so the meal-plan service can build the final plan from
    real recipes the agent actually found, without trusting the model to echo them back faithfully.
    """

    session: Session
    cp: ConstraintProfile
    profile_id: str
    servings: int
    surfaced: dict[uuid.UUID, Recipe] = field(default_factory=dict)


def _search_recipes(ctx: ToolContext, args: SearchRecipesInput) -> dict[str, Any]:
    """`search_recipes`: return up to k wall-cleared cards and remember the rows for plan assembly.

    Delegates to `rag.fresh_cards` (embed -> vector search pre-filtered by category/diet/seen -> wall ->
    cards, recording surfaced ids as seen). The real rows are stashed on the context so the meal-plan
    service can reason over cuisine/ingredients; the model only receives the cards.
    """
    recipes, cards = rag.fresh_cards(
        ctx.session, args.query, ctx.cp, ctx.profile_id, category=args.category, k=args.k
    )
    for recipe in recipes:
        ctx.surfaced[recipe.id] = recipe
    return {"cards": [card.model_dump() for card in cards]}


def _get_recipe(ctx: ToolContext, args: GetRecipeInput) -> dict[str, Any]:
    """`get_recipe`: one recipe's verbatim detail + scaled nutrition, or an honest not-available.

    Fetches the row and builds the detail ONLY through `recipe_view.to_detail`, which runs the wall first
    and returns None for a withheld recipe — surfaced to the model as a neutral "not available" so the
    wall is never bypassed and existence never leaks. A missing row / missing nutrition is the same answer.
    """
    recipe = repo_recipes.get_by_id(ctx.session, args.recipe_id)
    if recipe is None or recipe.nutrition is None:
        return {"error": "recipe not found"}
    nutrition = nutrition_service.scale(recipe.nutrition, ctx.servings)
    is_favorite = repo_favorites.exists(ctx.session, ctx.profile_id, args.recipe_id)
    detail = recipe_view.to_detail(recipe, ctx.cp, is_favorite=is_favorite, nutrition=nutrition)
    if detail is None:
        return {"error": "recipe not available"}  # wall withheld it — no leak
    ctx.surfaced[recipe.id] = recipe
    return {"recipe": detail.model_dump()}


def _get_nutrition(ctx: ToolContext, args: GetNutritionInput) -> dict[str, Any]:
    """`get_nutrition`: scaled nutrition for one recipe, gated by the wall (no data leak for a violator).

    Reads the cached nutrition and rescales it to the cook's servings. A recipe the wall withholds (or one
    with no stored nutrition) yields an honest "not available" rather than leaking its numbers.
    """
    recipe = repo_recipes.get_by_id(ctx.session, args.recipe_id)
    if recipe is None or recipe.nutrition is None:
        return {"error": "no nutrition available"}
    if constraint_guard.violates(recipe, ctx.cp):
        return {"error": "recipe not available"}  # wall governs the nutrition path too
    nutrition = nutrition_service.scale(recipe.nutrition, ctx.servings)
    return {"nutrition": nutrition.model_dump()}


def _build_shopping_list(ctx: ToolContext, args: BuildShoppingListInput) -> dict[str, Any]:
    """`build_shopping_list`: one consolidated, scaled list over the given recipes (violators excluded).

    Resolves each id to a row (preferring the already-fetched surfaced rows), drops any the wall withholds
    so a violating recipe's ingredients never enter the list, and hands the survivors to the deterministic
    `shopping_list.build` — which dedupes, merges compatible units, and scales to the cook's servings.
    """
    recipes: list[Recipe] = []
    for recipe_id in args.recipe_ids:
        recipe = ctx.surfaced.get(recipe_id) or repo_recipes.get_by_id(ctx.session, recipe_id)
        if recipe is None or constraint_guard.violates(recipe, ctx.cp):
            continue  # skip missing or wall-withheld recipes — never aggregate an unsafe one
        recipes.append(recipe)
    return {"shopping_list": shopping_list.build(recipes, ctx.servings).model_dump()}


def _substitute_ingredient(ctx: ToolContext, args: SubstituteIngredientInput) -> dict[str, Any]:
    """`substitute_ingredient`: curated, allergen-safe swaps for one ingredient (never invented).

    Delegates to the `substitution` service, which looks the ingredient up in the curated table and drops
    any swap that introduces one of the cook's declared allergens (fail-closed via `ctx.cp`). The model
    only ever sees curated, allergen-safe names or an honest `none_safe`, so the agent can fix an
    unsafe ingredient in a plan without the LLM inventing a replacement. `recipe_id` is accepted by the
    schema for context but the curated lookup is recipe-independent, so it is not needed here.
    """
    result = substitution.suggest(args.ingredient, ctx.cp)
    return {"substitution": result.model_dump()}


# Validation model + handler for each tool name. dispatch() looks both up by the LLM-provided name.
_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "search_recipes": SearchRecipesInput,
    "get_recipe": GetRecipeInput,
    "get_nutrition": GetNutritionInput,
    "build_shopping_list": BuildShoppingListInput,
    "substitute_ingredient": SubstituteIngredientInput,
}
_HANDLERS: dict[str, Callable[[ToolContext, Any], dict[str, Any]]] = {
    "search_recipes": _search_recipes,
    "get_recipe": _get_recipe,
    "get_nutrition": _get_nutrition,
    "build_shopping_list": _build_shopping_list,
    "substitute_ingredient": _substitute_ingredient,
}

# The five fixed categories, surfaced to the model as the only legal `category` values (FR convention).
_CATEGORY_ENUM = ["hot_drink", "cold_drink", "breakfast", "lunch", "dinner"]

# Groq/OpenAI native function-calling specs — the tool surface advertised to the model. Kept in lockstep
# with `_INPUT_MODELS`; the real validation is still the Pydantic model in dispatch (these only guide the
# model). recipe ids are described as strings (the model echoes back ids it received from search results).
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_recipes",
            "description": "Semantic search for up to k real, safe recipe cards, optionally within one category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to cook (cuisine, dish, ingredients)."},
                    "category": {"type": "string", "enum": _CATEGORY_ENUM},
                    "k": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": "Fetch one recipe's full detail (verbatim steps + scaled nutrition) by its id.",
            "parameters": {
                "type": "object",
                "properties": {"recipe_id": {"type": "string", "description": "A recipe id from a prior search."}},
                "required": ["recipe_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nutrition",
            "description": "Scaled nutrition summary (calories + macros) for one recipe id.",
            "parameters": {
                "type": "object",
                "properties": {"recipe_id": {"type": "string", "description": "A recipe id from a prior search."}},
                "required": ["recipe_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_shopping_list",
            "description": "Build one consolidated, deduplicated, serving-scaled shopping list for these recipe ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 14,
                    }
                },
                "required": ["recipe_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "substitute_ingredient",
            "description": (
                "Curated, allergen-safe replacements for one ingredient (e.g. butter). "
                "Returns only safe suggestions or none_safe; never invents a substitute."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": "string", "description": "The ingredient to replace (1-60 chars)."},
                    "recipe_id": {"type": "string", "description": "Optional recipe context id."},
                },
                "required": ["ingredient"],
            },
        },
    },
]


def dispatch(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Validate then run one tool call, returning a JSON-serializable result (or a safe error dict).

    The single entry point the loop uses for every tool call. Looks the tool up by the model-supplied
    `name`; an unknown name is rejected. The raw `arguments` are parsed through the tool's Pydantic input
    model FIRST (FR-027) — a `ValidationError` (out-of-range k, missing field, malformed id, oversized
    list) returns an `error` dict that the loop feeds back to the model, never an executed call. Only on
    valid input does the matching handler run with the trusted `ctx`.
    """
    model = _INPUT_MODELS.get(name)
    handler = _HANDLERS.get(name)
    if model is None or handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        args = model(**arguments)
    except ValidationError as exc:
        return {"error": "invalid tool input", "details": exc.errors()}
    return handler(ctx, args)
