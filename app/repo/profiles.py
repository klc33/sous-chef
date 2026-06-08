"""Profile data access — the ONLY place profile rows are read or written (ORM/parameterized only).

`get` returns the stored row or None (the service layer applies defaults when None). `upsert` creates
or updates the row keyed by the profile-ID (which comes from the header, never the body).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.profile import Profile


def get(session: Session, profile_id: str) -> Profile | None:
    """Return the Profile row for this profile-ID, or None when the cook has never been stored."""
    return session.execute(
        select(Profile).where(Profile.profile_id == profile_id)
    ).scalar_one_or_none()


def upsert(
    session: Session,
    profile_id: str,
    *,
    diet: str,
    allergies: list[str],
    default_servings: int,
) -> Profile:
    """Create or update the cook's constraints for this profile-ID, returning the persisted row.

    Looks up the existing row; updates its fields in place or inserts a new one. `updated_at` is
    maintained by the ORM `onupdate`. Flushes in the caller's transaction.
    """
    profile = get(session, profile_id)
    if profile is None:
        profile = Profile(profile_id=profile_id)
        session.add(profile)
    profile.diet = diet
    profile.allergies = allergies
    profile.default_servings = default_servings
    session.flush()
    return profile
