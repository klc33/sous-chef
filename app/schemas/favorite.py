"""Pydantic request model for saving a favorite.

Mirrors contracts/favorites.openapi.yaml. Like every other cook-facing body, the owner (profile-ID) is
NEVER part of this payload — it comes from the X-Profile-ID header via api/deps.py. `recipe_id` is kept
as a plain string here so the router can draw the contract's own distinction between a malformed id (400)
and a well-formed-but-unknown id (404), rather than collapsing both into Pydantic's 422.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["FavoriteIn"]


class FavoriteIn(BaseModel):
    """Incoming body on POST /favorites: the id of the recipe to save (validated as a UUID in the router)."""

    recipe_id: str
