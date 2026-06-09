"""Unit tests for THE WALL (services/user/constraint_guard.py) — golden rule #1.

These pin the deterministic predicate that decides whether a recipe may surface: an allergen hit, the
fail-closed rule on uncertain allergen detection, each diet, the never-filtering `diet=none`, and that
`filter` drops violators while keeping compliant recipes in order. Pure Python — no DB, no app.

This file pins the predicate in isolation. The companion "new output path forgets the guard" regression
(T039) — which proves every wired cook-facing endpoint actually invokes the guard — lives in
`tests/integration/test_wall_regression.py`, since it must drive the real DB-backed HTTP paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.recipe import Diet
from app.services.user.constraint_guard import ConstraintProfile, filter, violates


@dataclass
class FakeRecipe:
    """A minimal recipe stand-in exposing exactly the fields the wall reads.

    Defaults describe a permissive recipe (no allergens, certain detection, satisfies every diet) so a
    test sets only the one attribute it is probing.
    """

    allergens: list[str] = field(default_factory=list)
    allergen_certain: bool = True
    is_vegetarian: bool = True
    is_vegan: bool = True
    is_pescatarian: bool = True


def test_violates_on_allergen_hit() -> None:
    """A declared allergy present in the recipe's detected allergens is a violation."""
    recipe = FakeRecipe(allergens=["peanuts"])
    cp = ConstraintProfile(diet=Diet.NONE, allergies=frozenset({"peanuts"}))
    assert violates(recipe, cp) is True


def test_no_violation_when_allergen_absent() -> None:
    """An allergy the recipe does not contain (and certain detection) is not a violation."""
    recipe = FakeRecipe(allergens=["milk"], allergen_certain=True)
    cp = ConstraintProfile(diet=Diet.NONE, allergies=frozenset({"peanuts"}))
    assert violates(recipe, cp) is False


def test_fail_closed_on_uncertain_allergen_detection() -> None:
    """An allergic cook must NOT see a recipe whose allergen detection was uncertain (fail-closed)."""
    recipe = FakeRecipe(allergens=[], allergen_certain=False)
    cp = ConstraintProfile(diet=Diet.NONE, allergies=frozenset({"peanuts"}))
    assert violates(recipe, cp) is True


def test_uncertain_detection_ok_when_no_allergies() -> None:
    """Uncertain detection is harmless when the cook declared no allergies — nothing to fail closed on."""
    recipe = FakeRecipe(allergens=[], allergen_certain=False)
    cp = ConstraintProfile.default()
    assert violates(recipe, cp) is False


def test_vegan_requires_vegan_flag() -> None:
    """A vegan cook sees a recipe only when it is flagged vegan."""
    cp = ConstraintProfile(diet=Diet.VEGAN, allergies=frozenset())
    assert violates(FakeRecipe(is_vegan=False), cp) is True
    assert violates(FakeRecipe(is_vegan=True), cp) is False


def test_vegetarian_requires_vegetarian_flag() -> None:
    """A vegetarian cook sees a recipe only when it is flagged vegetarian."""
    cp = ConstraintProfile(diet=Diet.VEGETARIAN, allergies=frozenset())
    assert violates(FakeRecipe(is_vegetarian=False), cp) is True
    assert violates(FakeRecipe(is_vegetarian=True), cp) is False


def test_pescatarian_requires_pescatarian_flag() -> None:
    """A pescatarian cook sees a recipe only when it is flagged pescatarian."""
    cp = ConstraintProfile(diet=Diet.PESCATARIAN, allergies=frozenset())
    assert violates(FakeRecipe(is_pescatarian=False), cp) is True
    assert violates(FakeRecipe(is_pescatarian=True), cp) is False


def test_diet_none_never_filters() -> None:
    """`diet=none` imposes no diet constraint, even on a recipe failing every diet flag."""
    recipe = FakeRecipe(is_vegetarian=False, is_vegan=False, is_pescatarian=False)
    cp = ConstraintProfile.default()
    assert violates(recipe, cp) is False


def test_filter_drops_violators_keeps_compliant_in_order() -> None:
    """`filter` removes violating recipes and preserves the order of the compliant survivors."""
    safe_a = FakeRecipe(allergens=["milk"])
    bad = FakeRecipe(allergens=["peanuts"])
    safe_b = FakeRecipe(allergens=[])
    cp = ConstraintProfile(diet=Diet.NONE, allergies=frozenset({"peanuts"}))

    result = filter([safe_a, bad, safe_b], cp)

    assert result == [safe_a, safe_b]
