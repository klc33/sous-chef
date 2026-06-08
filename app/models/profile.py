"""ORM models for the passwordless cook: Profile, Favorite, SeenHistory.

A Profile is keyed by the opaque `X-Profile-ID` header value (never taken from a request body) and
holds the constraints that drive the wall. Favorites are idempotent by composite PK. SeenHistory is
created here so the later freshness phase can build on it, but no read/write behavior is wired in this
feature. See specs/002-catalog-wall-favorites/data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Profile(Base):
    """A cook's stored constraints, keyed by the passwordless profile-ID header value.

    Defaults (diet=none, no allergies, servings=2) are applied at the service layer when no row
    exists yet, so a never-seen profile-ID reads as the permissive default without a write.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        # diet is a constrained string matching the Diet StrEnum values.
        CheckConstraint(
            "diet IN ('none', 'vegetarian', 'vegan', 'pescatarian')",
            name="ck_profiles_diet",
        ),
    )

    # Opaque client-generated id (e.g. a UUID in widget localStorage); the owner key, never from body.
    profile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    diet: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    allergies: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    default_servings: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Favorite(Base):
    """A cook's saved recipe. The composite PK `(profile_id, recipe_id)` makes saving idempotent.

    FKs cascade on delete so removing a profile or recipe cleans up its favorites automatically.
    """

    __tablename__ = "favorites"
    __table_args__ = (Index("ix_favorites_profile_id", "profile_id"),)

    profile_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("profiles.profile_id", ondelete="CASCADE"),
        primary_key=True,
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SeenHistory(Base):
    """Per-profile record of shown recipes — created for the future freshness phase, inert this phase.

    No code reads or writes this table in 002; it exists so the freshness migration/queries can build
    on a stable schema later (data-model.md).
    """

    __tablename__ = "seen_history"
    __table_args__ = (Index("ix_seen_history_profile_shown", "profile_id", "shown_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("profiles.profile_id", ondelete="CASCADE"),
        nullable=False,
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    shown_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
