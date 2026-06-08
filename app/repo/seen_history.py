"""Seen-history data access — DEFINED but UNUSED this phase.

The freshness feature (a later phase) will exclude already-shown recipes from retrieval using this
table. It is created now so that phase has a stable schema/repo to build on, but nothing in 002 calls
these functions — the catalog/wall/favorites surface does no freshness filtering (data-model.md).
"""

from __future__ import annotations

import builtins
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.profile import SeenHistory


def insert(session: Session, profile_id: str, recipe_id: uuid.UUID) -> None:
    """Record that a recipe was shown to a profile. Inert this phase — wired by the freshness feature."""
    session.add(SeenHistory(profile_id=profile_id, recipe_id=recipe_id))
    session.flush()


def list(session: Session, profile_id: str) -> builtins.list[SeenHistory]:
    """Return a profile's seen-history rows. Inert this phase — wired by the freshness feature."""
    rows = session.execute(
        select(SeenHistory)
        .where(SeenHistory.profile_id == profile_id)
        .order_by(SeenHistory.shown_at.desc())
    ).scalars()
    # `[*rows]` rather than `list(rows)` — the function is named `list`, which shadows the builtin here.
    return [*rows]
