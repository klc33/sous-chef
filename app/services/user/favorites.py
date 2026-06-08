"""Favorites business logic — save, list, and remove a cook's saved recipes, with the wall on the list.

Thin orchestration over `repo.favorites` plus the wall. Saving and removing inherit the repo's
idempotency (the composite PK makes a duplicate save a no-op; deleting a missing favorite is a no-op),
so the API can answer uniformly. Listing resolves the cook's current ConstraintProfile and routes every
saved recipe through `recipe_view.to_cards` — the same choke point every other list path uses — so a
favorite that now violates a newly-declared allergy or diet is silently omitted (golden rule #1: the
wall holds on every path, favorites included).
"""

from __future__ import annotations

import builtins
import uuid

from sqlalchemy.orm import Session

from app.repo import favorites as repo_favorites
from app.repo import profiles as repo_profiles
from app.schemas.recipe import RecipeCard
from app.services.shared import recipe_view
from app.services.user.constraint_guard import ConstraintProfile


def save(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Save a recipe to the cook's favorites; a repeat save of the same pair is a no-op (idempotent).

    Ensures the cook's profile row exists first (saving is often a new cook's first action) so the
    `favorites.profile_id` FK holds without requiring a prior PUT /profile; an existing profile's
    constraints are left untouched.
    """
    repo_profiles.ensure_exists(session, profile_id)
    repo_favorites.add(session, profile_id, recipe_id)


def list(session: Session, profile_id: str) -> builtins.list[RecipeCard]:
    """Return the cook's favorites as wall-filtered cards, newest first; violating saves are omitted.

    Resolves the ConstraintProfile from the stored profile (permissive default for an unknown cook),
    fetches the saved recipes, and builds cards ONLY through recipe_view.to_cards — which runs the guard
    first. A favorite saved before an allergy was declared therefore drops out of the list automatically.
    """
    profile = repo_profiles.get(session, profile_id)
    cp = ConstraintProfile.from_row(profile) if profile is not None else ConstraintProfile.default()
    recipes = repo_favorites.list(session, profile_id)
    return recipe_view.to_cards(recipes, cp)


def remove(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Remove a recipe from the cook's favorites; removing one that is not saved is a no-op (idempotent)."""
    repo_favorites.remove(session, profile_id, recipe_id)
