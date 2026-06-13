"""Unit tests for nutrition scaling (services/user/nutrition.scale).

Pure-function tests: rescaling stored totals from their basis servings to the cook's servings (up, down,
and identity) and the `is_approximate` flag passing through untouched. No DB — a detached NutritionCache
instance is just an attribute bag here.
"""

from __future__ import annotations

from app.models.recipe import NutritionCache
from app.services.user.nutrition import scale


def _cache(**overrides: object) -> NutritionCache:
    """Build a detached NutritionCache row with sensible defaults, overridable per test."""
    base: dict[str, object] = {
        "basis_servings": 2,
        "calories": 400,
        "protein_g": 20,
        "carbs_g": 50,
        "fat_g": 10,
        "is_approximate": False,
        "unmapped_ingredient_count": 0,
    }
    base.update(overrides)
    return NutritionCache(**base)


def test_scales_up_from_basis_to_cook_servings() -> None:
    """Doubling servings (2 → 4) doubles every macro and reports the cook's servings."""
    summary = scale(_cache(), cook_servings=4)
    assert summary.servings == 4
    assert summary.calories == 800
    assert summary.protein_g == 40
    assert summary.carbs_g == 100
    assert summary.fat_g == 20


def test_scales_down_from_basis_to_cook_servings() -> None:
    """Halving servings (4 → 2) halves the totals."""
    summary = scale(_cache(basis_servings=4, calories=800), cook_servings=2)
    assert summary.calories == 400
    assert summary.servings == 2


def test_same_servings_is_identity() -> None:
    """Cooking for the basis servings leaves the totals unchanged."""
    summary = scale(_cache(), cook_servings=2)
    assert summary.servings == 2
    assert summary.calories == 400


def test_is_approximate_passthrough_true() -> None:
    """An approximate cache stays approximate after scaling — rescaling never makes it exact."""
    assert scale(_cache(is_approximate=True), 4).is_approximate is True


def test_is_approximate_passthrough_false() -> None:
    """An exact cache stays exact after scaling."""
    assert scale(_cache(is_approximate=False), 4).is_approximate is False


def test_unmapped_count_passthrough() -> None:
    """Unmapped-ingredient count describes coverage, so it carries through scaling unchanged."""
    assert scale(_cache(unmapped_ingredient_count=3), cook_servings=8).unmapped_ingredient_count == 3
