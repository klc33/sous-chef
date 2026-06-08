"""GET/PUT /profile — the passwordless cook's diet, allergies, and default servings.

Identity is the X-Profile-ID header (via deps.require_profile_id); the owner is never read from the
body. GET returns the permissive defaults (diet=none, no allergies, servings=2) when the cook has never
been stored, so a brand-new profile-ID reads cleanly without a prior write. PUT validates the body
(unknown diet/allergen rejected by the enum fields; servings >= 1) and upserts. Mirrors
contracts/profile.openapi.yaml.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_profile_id
from app.models.profile import Profile
from app.models.recipe import Allergen, Diet
from app.repo import profiles as repo_profiles
from app.schemas.profile import ProfileIn, ProfileOut

router = APIRouter()

# Annotated dependency aliases (the modern FastAPI idiom — keeps Depends out of default values).
ProfileId = Annotated[str, Depends(require_profile_id)]
DbSession = Annotated[Session, Depends(get_db)]

# The defaults a never-set profile reads as: no diet filtering, no allergies, two servings.
_DEFAULT_PROFILE = ProfileOut(diet=Diet.NONE, allergies=[], default_servings=2)


def _to_out(profile: Profile) -> ProfileOut:
    """Build the ProfileOut response from a stored profiles row (enums coerced from the stored strings)."""
    return ProfileOut(
        diet=Diet(profile.diet),
        allergies=[Allergen(a) for a in profile.allergies],
        default_servings=profile.default_servings,
    )


@router.get("/profile", response_model=ProfileOut)
def get_profile(profile_id: ProfileId, session: DbSession) -> ProfileOut:
    """Return the cook's stored constraints, or the permissive defaults when never set.

    A missing row is not an error — an unknown cook simply has nothing for the wall to enforce, so we
    return the defaults rather than 404.
    """
    profile = repo_profiles.get(session, profile_id)
    if profile is None:
        return _DEFAULT_PROFILE
    return _to_out(profile)


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfileIn, profile_id: ProfileId, session: DbSession) -> ProfileOut:
    """Validate and upsert the cook's constraints, returning the saved profile.

    The enum-typed body fields reject unknown diets/allergens at validation time (422) and servings is
    bounded >= 1; enum values are stored as their plain strings via the repo. The new constraints take
    effect on every subsequent recipe path (the wall reads this profile).
    """
    profile = repo_profiles.upsert(
        session,
        profile_id,
        diet=body.diet.value,
        allergies=[a.value for a in body.allergies],
        default_servings=body.default_servings,
    )
    return _to_out(profile)
