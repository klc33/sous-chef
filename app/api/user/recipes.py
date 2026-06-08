"""GET /recipes?category= and GET /recipes/{id} — real, wall-filtered recipe cards and detail.

The flow is deterministic and grounded: resolve the cook's ConstraintProfile from the stored profile
(or the permissive default), pull only complete recipes in the requested category from the repo, then
build cards/detail ONLY through recipe_view — the single choke point that runs the wall first. A
category with no compliant recipe returns an honest empty list, never a substitute; a recipe the wall
withholds is returned as 404 (indistinguishable from non-existent, so the detail path neither bypasses
the wall nor leaks existence). Steps render verbatim. Mirrors contracts/recipes.openapi.yaml.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_profile_id
from app.core.errors import AppError
from app.models.recipe import Category
from app.repo import favorites as repo_favorites
from app.repo import profiles as repo_profiles
from app.repo import recipes as repo_recipes
from app.schemas.recipe import RecipeCard, RecipeDetail
from app.services.shared import recipe_view
from app.services.user import nutrition as nutrition_service
from app.services.user.constraint_guard import ConstraintProfile

router = APIRouter()

# Annotated dependency aliases (keeps Depends out of default values; the modern FastAPI idiom).
ProfileId = Annotated[str, Depends(require_profile_id)]
DbSession = Annotated[Session, Depends(get_db)]

# Servings a never-set profile cooks for — mirrors the GET /profile default so detail nutrition scales
# consistently for an unknown cook.
_DEFAULT_SERVINGS = 2


def _parse_category(raw: str | None) -> Category:
    """Validate the category query param into the Category enum, or raise a 400 (per the contract).

    A missing/blank/unknown category is a client error, answered as 400 with a machine code rather than
    FastAPI's default 422, so the recipe surface speaks one error shape for bad input.
    """
    if raw is None or not raw.strip():
        raise AppError("Missing category query parameter.", status_code=400, code="missing_category")
    try:
        return Category(raw)
    except ValueError:
        raise AppError(
            f"Unknown category '{raw}'.", status_code=400, code="invalid_category"
        ) from None


@router.get("/recipes", response_model=list[RecipeCard])
def list_recipes(
    profile_id: ProfileId,
    session: DbSession,
    category: Annotated[str | None, Query()] = None,
) -> list[RecipeCard]:
    """Return compliant cards for one category, filtered by the wall against the cook's constraints.

    Resolves the ConstraintProfile from the stored profile (default for an unknown cook), fetches the
    complete recipes in the category, and hands them to recipe_view.to_cards — which runs the guard
    before building any card. The result MAY be empty when nothing satisfies the constraints; that is
    the honest answer.
    """
    cat = _parse_category(category)
    profile = repo_profiles.get(session, profile_id)
    cp = ConstraintProfile.from_row(profile) if profile is not None else ConstraintProfile.default()
    recipes = repo_recipes.list_by_category(session, cat.value)
    return recipe_view.to_cards(recipes, cp)


def _parse_recipe_id(raw: str) -> uuid.UUID:
    """Parse the path id as a UUID, mapping a malformed id to 404 (a non-existent recipe, no leak).

    A bad id is treated as "not found" rather than a 422 so the detail surface speaks only 200/400/404
    and never reveals more than existence would (which it also hides — see the wall below).
    """
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise _not_found() from None


def _not_found() -> AppError:
    """Build the uniform 404 used for both a missing recipe and one the wall withholds (no leak)."""
    return AppError("Recipe not found.", status_code=404, code="recipe_not_found")


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
def get_recipe(recipe_id: str, profile_id: ProfileId, session: DbSession) -> RecipeDetail:
    """Return one recipe's verbatim steps + nutrition scaled to the cook's servings, subject to the wall.

    Resolves the cook's ConstraintProfile and servings from the stored profile (defaults for an unknown
    cook), fetches the recipe, and routes it through recipe_view.to_detail — which runs the guard first
    and returns None when the recipe violates the constraints. A missing recipe, a recipe with no stored
    nutrition (i.e. not surfaceable), and a withheld recipe ALL answer 404, so the wall is never
    bypassed and existence never leaks. Steps are rendered exactly as stored (golden rule #2).
    """
    rid = _parse_recipe_id(recipe_id)
    profile = repo_profiles.get(session, profile_id)
    cp = ConstraintProfile.from_row(profile) if profile is not None else ConstraintProfile.default()
    cook_servings = profile.default_servings if profile is not None else _DEFAULT_SERVINGS

    recipe = repo_recipes.get_by_id(session, rid)
    # No row, or an incomplete row with no precomputed nutrition → not surfaceable → 404.
    if recipe is None or recipe.nutrition is None:
        raise _not_found()

    is_favorite = repo_favorites.exists(session, profile_id, rid)
    nutrition = nutrition_service.scale(recipe.nutrition, cook_servings)
    detail = recipe_view.to_detail(recipe, cp, is_favorite=is_favorite, nutrition=nutrition)
    # to_detail returns None when the wall withholds the recipe — same 404 as "missing" (no leak).
    if detail is None:
        raise _not_found()
    return detail
