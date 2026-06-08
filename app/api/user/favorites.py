"""POST/GET/DELETE /favorites — per-cook saved recipes, persisted across sessions by X-Profile-ID.

The owner is always the header profile-ID (never the body). Saving is idempotent (a repeat save is the
same 201, no duplicate) and validated against the corpus: an unknown recipe_id is a 404, a malformed one
a 400 — the contract's two distinct bad-input cases. Listing builds cards ONLY through the favorites
service, which runs the wall, so a saved recipe that now violates the cook's constraints is omitted.
Removal is idempotent (deleting a not-saved recipe still answers 204). Mirrors
contracts/favorites.openapi.yaml.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_profile_id
from app.core.errors import AppError
from app.repo import recipes as repo_recipes
from app.schemas.favorite import FavoriteIn
from app.schemas.recipe import RecipeCard
from app.services.user import favorites as favorites_service

router = APIRouter()

# Annotated dependency aliases (keeps Depends out of default values; the modern FastAPI idiom).
ProfileId = Annotated[str, Depends(require_profile_id)]
DbSession = Annotated[Session, Depends(get_db)]


def _parse_recipe_id(raw: str) -> uuid.UUID:
    """Parse a recipe id string into a UUID, mapping a malformed value to a 400 (per the contract).

    The save path must tell apart a malformed id (400) from a well-formed-but-unknown one (404), so the
    parse failure is its own client error rather than being folded into "not found".
    """
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise AppError(
            "Invalid recipe_id.", status_code=400, code="invalid_recipe_id"
        ) from None


@router.post("/favorites", status_code=status.HTTP_201_CREATED)
def save_favorite(body: FavoriteIn, profile_id: ProfileId, session: DbSession) -> None:
    """Save a recipe to the cook's favorites (idempotent): 400 malformed id, 404 unknown, else 201.

    Confirms the recipe exists in the corpus before saving so a bad id never becomes a dangling favorite;
    a repeat save returns the same 201 because the service/repo treat it as a no-op (FR-018).
    """
    recipe_id = _parse_recipe_id(body.recipe_id)
    # Existence check: a save must reference a real recipe (404), distinct from a malformed id (400).
    if repo_recipes.get_by_id(session, recipe_id) is None:
        raise AppError("Recipe not found.", status_code=404, code="recipe_not_found")
    favorites_service.save(session, profile_id, recipe_id)


@router.get("/favorites", response_model=list[RecipeCard])
def list_favorites(profile_id: ProfileId, session: DbSession) -> list[RecipeCard]:
    """Return the cook's favorites as wall-filtered cards; a now-violating saved recipe is omitted.

    Delegates to the favorites service, which resolves the cook's current constraints and builds cards
    through recipe_view — so the wall applies to this list exactly as it does to browse.
    """
    return favorites_service.list(session, profile_id)


@router.delete("/favorites/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(recipe_id: str, profile_id: ProfileId, session: DbSession) -> None:
    """Remove a recipe from the cook's favorites (idempotent): always 204 for a valid profile.

    Removal is forgiving — a not-saved or malformed id simply has nothing to delete, so it still answers
    204 rather than surfacing an error (the contract documents only 204/400-profile here).
    """
    try:
        rid = uuid.UUID(recipe_id)
    except ValueError:
        # A malformed id cannot match any saved favorite — nothing to remove, idempotent no-op.
        return
    favorites_service.remove(session, profile_id, rid)
