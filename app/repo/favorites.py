"""Favorites data access — the ONLY place favorite rows are read or written (ORM/parameterized only).

Saving is idempotent (the composite PK makes a duplicate a no-op); listing returns the saved recipes
with ingredients eager-loaded so the service can run the wall and build cards.
"""

from __future__ import annotations

import builtins
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models.profile import Favorite
from app.models.recipe import Recipe


def add(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Save a favorite, idempotently. A second save of the same pair is a no-op (PK already present).

    Checks existence first rather than relying on an INSERT conflict, so re-saving never raises and the
    caller gets uniform 201 behavior (FR-018).
    """
    if exists(session, profile_id, recipe_id):
        return
    session.add(Favorite(profile_id=profile_id, recipe_id=recipe_id))
    session.flush()


def exists(session: Session, profile_id: str, recipe_id: uuid.UUID) -> bool:
    """Return True when this (profile, recipe) favorite is already stored."""
    row = session.execute(
        select(Favorite.recipe_id).where(
            Favorite.profile_id == profile_id, Favorite.recipe_id == recipe_id
        )
    ).first()
    return row is not None


def list(session: Session, profile_id: str) -> builtins.list[Recipe]:
    """Return the cook's favorited recipes (ingredients eager-loaded), newest save first.

    Joins favorites→recipes so the service can run the wall over real recipe rows and build cards. The
    wall (not this query) decides which favorites are still surfaceable.
    """
    rows = session.execute(
        select(Recipe)
        .join(Favorite, Favorite.recipe_id == Recipe.id)
        .where(Favorite.profile_id == profile_id)
        .options(selectinload(Recipe.ingredients))
        .order_by(Favorite.created_at.desc())
    ).scalars()
    # `[*rows]` rather than `list(rows)` — the function is named `list`, which shadows the builtin here.
    return [*rows]


def remove(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Delete a favorite if present; deleting a non-existent favorite is a silent no-op (idempotent)."""
    session.execute(
        delete(Favorite).where(
            Favorite.profile_id == profile_id, Favorite.recipe_id == recipe_id
        )
    )
    session.flush()
