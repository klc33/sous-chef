"""Seen-history data access — the freshness store, wired live in 003-intelligent-behavior.

A single per-cook set of already-shown recipe ids. `insert` records a surfaced recipe, `list` reads a
cook's rows, and `clear` wipes them for reset-on-exhaustion (when retrieval can no longer find `k`
unseen compliant recipes, the service clears history and re-queries so discovery never dead-ends).
Favorites are never written here and the favorites path never reads it (data-model.md invariant).
"""

from __future__ import annotations

import builtins
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.profile import SeenHistory


def insert(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Record that a recipe was shown to a cook so freshness can exclude it from future retrievals."""
    session.add(SeenHistory(profile_id=profile_id, recipe_id=recipe_id))
    session.flush()


def list(session: Session, profile_id: str) -> builtins.list[SeenHistory]:
    """Return a cook's seen-history rows, most-recent first (the exclusion set source)."""
    rows = session.execute(
        select(SeenHistory)
        .where(SeenHistory.profile_id == profile_id)
        .order_by(SeenHistory.shown_at.desc())
    ).scalars()
    # `[*rows]` rather than `list(rows)` — the function is named `list`, which shadows the builtin here.
    return [*rows]


def clear(session: Session, profile_id: str) -> None:
    """Delete all of a cook's seen-history rows (reset-on-exhaustion).

    Called by freshness when retrieval can no longer surface `k` unseen compliant recipes: wiping the set
    lets the same query start returning recipes again instead of an empty result. A single parameterized
    DELETE scoped to this `profile_id` only — never touches another cook's history. Flushes in the
    caller's transaction.
    """
    session.execute(delete(SeenHistory).where(SeenHistory.profile_id == profile_id))
    session.flush()
