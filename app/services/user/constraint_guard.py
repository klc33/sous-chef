"""THE WALL — the deterministic safety guard that decides whether a recipe may be shown to a cook.

This is the grade (golden rule #1): a recipe that violates a cook's allergy or diet must NEVER surface,
and that decision is made here in plain, auditable Python — never in a prompt or a model. The module is
pure and deterministic: given the same recipe and constraint profile it always returns the same answer.

Fail-closed is the core safety stance: if a cook has any allergy and a recipe's allergen detection was
uncertain (`allergen_certain = false`), the recipe is treated as a violation — uncertainty counts
against surfacing, never for it. `diet = none` never filters on diet.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.models.recipe import Diet


class _RecipeLike(Protocol):
    """The minimal recipe shape the wall reads — lets us guard ORM rows or any equivalent object."""

    allergens: list[str]
    allergen_certain: bool
    is_vegetarian: bool
    is_vegan: bool
    is_pescatarian: bool


@dataclass(frozen=True)
class ConstraintProfile:
    """A cook's resolved constraints (diet + allergy set) — the only input the wall needs besides a recipe.

    Frozen/value object so it can't be mutated mid-request. Built via `default()` for an unknown cook or
    `from_row(profile)` from a stored profile row.
    """

    diet: Diet = Diet.NONE
    allergies: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def default(cls) -> ConstraintProfile:
        """The permissive default for a cook who has never set constraints: no diet, no allergies.

        Permissive on filtering is correct here — a cook with no stated allergies has nothing the wall
        must protect against; the safety stance only tightens once allergies/diet are declared.
        """
        return cls(diet=Diet.NONE, allergies=frozenset())

    @classmethod
    def from_row(cls, profile: object) -> ConstraintProfile:
        """Build a ConstraintProfile from a stored profiles row (its `diet` and `allergies` fields)."""
        diet = Diet(getattr(profile, "diet", Diet.NONE))
        allergies = frozenset(getattr(profile, "allergies", []) or [])
        return cls(diet=diet, allergies=allergies)


def violates(recipe: _RecipeLike, cp: ConstraintProfile) -> bool:
    """Return True when `recipe` violates the cook's constraints and so must NOT be shown.

    Implements the data-model predicate exactly:
      * a declared allergy intersects the recipe's detected allergens, OR
      * the cook has any allergy AND the recipe's allergen detection was uncertain (fail-closed), OR
      * the cook's diet (vegan/vegetarian/pescatarian) is not satisfied by the matching recipe flag.
    `diet = none` never contributes a violation.
    """
    # Allergen hit: any declared allergy present in the recipe's detected set.
    if cp.allergies & set(recipe.allergens):
        return True

    # Fail-closed: an allergic cook must not see a recipe whose allergen status is uncertain.
    if cp.allergies and not recipe.allergen_certain:
        return True

    # Diet: a stricter diet is satisfied only when the corresponding precomputed flag is true.
    return (
        (cp.diet == Diet.VEGAN and not recipe.is_vegan)
        or (cp.diet == Diet.VEGETARIAN and not recipe.is_vegetarian)
        or (cp.diet == Diet.PESCATARIAN and not recipe.is_pescatarian)
    )


def filter[T: _RecipeLike](recipes: Iterable[T], cp: ConstraintProfile) -> list[T]:
    """Return only the recipes that do NOT violate the cook's constraints, preserving input order.

    The single helper every list path uses to enforce the wall over a collection; built on `violates`
    so list and single-item decisions can never diverge.
    """
    return [r for r in recipes if not violates(r, cp)]
