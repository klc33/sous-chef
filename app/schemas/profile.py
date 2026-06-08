"""Pydantic request/response models for the cook profile (diet, allergies, servings).

Mirrors contracts/profile.openapi.yaml. The owner (profile-ID) is NEVER part of these bodies — it is
read from the X-Profile-ID header via api/deps.py. Diet/Allergen reuse the domain StrEnums so unknown
values are rejected at validation time (a 422/400), satisfying the "validate diet/allergens" rule.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.recipe import Allergen, Diet

__all__ = ["ProfileIn", "ProfileOut"]


class ProfileIn(BaseModel):
    """Incoming constraints on PUT /profile. Enum fields reject unknown diets/allergens automatically."""

    diet: Diet = Diet.NONE
    allergies: list[Allergen] = Field(default_factory=list)
    default_servings: int = Field(default=2, ge=1)


class ProfileOut(BaseModel):
    """Outgoing constraints on GET/PUT /profile (defaults when the cook has never set them)."""

    diet: Diet
    allergies: list[Allergen]
    default_servings: int
