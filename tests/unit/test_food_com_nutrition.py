"""Unit tests for the Food.com authoritative nutrition path (ingestion).

Covers three pure pieces: parsing the dataset's stringified `nutrition` cell (fetch_kaggle), converting
a per-serving PDV list into exact grams (nutrition.from_food_com), and the source-preference dispatch
(nutrition.compute) — authoritative Food.com data over the Open Food Facts approximation. No DB, no
network: the OFF adapter is a stub that fails the test if the authoritative path ever touches it.
"""

from __future__ import annotations

from typing import Any

import pytest
from ingestion import nutrition
from ingestion.fetch_kaggle import _parse_nutrition

# A representative Food.com per-serving nutrition row chosen for clean arithmetic:
# [calories, total fat PDV, sugar PDV, sodium PDV, protein PDV, saturated fat PDV, carbohydrates PDV].
_SAMPLE = [200.0, 10.0, 5.0, 8.0, 20.0, 4.0, 15.0]


class _ExplodingOFF:
    """OFF stand-in that raises if used — proves the authoritative path never aggregates from OFF."""

    def lookup_ingredient(self, name: str) -> dict[str, Any]:
        """Fail loudly: the Food.com path must not consult Open Food Facts."""
        raise AssertionError("OFF was queried on the authoritative Food.com nutrition path")


def test_from_food_com_converts_pdv_to_grams_exactly() -> None:
    """PDV macros convert to grams via their reference DVs; calories pass through; basis is 1 serving."""
    out = nutrition.from_food_com(_SAMPLE)
    assert out["basis_servings"] == 1
    assert out["calories"] == 200.0
    assert out["fat_g"] == 6.5  # 10% of 65 g
    assert out["protein_g"] == 10.0  # 20% of 50 g
    assert out["carbs_g"] == 45.0  # 15% of 300 g


def test_from_food_com_is_exact_not_approximate() -> None:
    """Authoritative source totals are marked exact, with nothing unmapped (the approximation fix)."""
    out = nutrition.from_food_com(_SAMPLE)
    assert out["is_approximate"] is False
    assert out["unmapped_ingredient_count"] == 0


def test_compute_prefers_food_com_over_off() -> None:
    """When the recipe carries Food.com nutrition, compute uses it and never touches OFF."""
    raw = {"food_com_nutrition": _SAMPLE}
    # Ingredients are present too, yet the authoritative source must win (OFF would explode if reached).
    out = nutrition.compute(raw, [{"name": "x"}], _ExplodingOFF(), basis_servings=4)
    assert out is not None
    assert out["is_approximate"] is False
    assert out["calories"] == 200.0


def test_compute_returns_none_without_source_or_ingredients() -> None:
    """No source nutrition and no ingredients → nothing to compute (recipe stays incomplete)."""
    assert nutrition.compute({}, [], _ExplodingOFF(), basis_servings=2) is None


@pytest.mark.parametrize(
    "cell",
    [
        "[200.0, 10.0, 5.0, 8.0, 20.0, 4.0, 15.0]",  # the CSV stringified-list form
        [200.0, 10.0, 5.0, 8.0, 20.0, 4.0, 15.0],  # an already-parsed list
    ],
)
def test_parse_nutrition_accepts_valid_seven_tuple(cell: object) -> None:
    """A well-formed 7-element cell (string or list) parses to seven floats."""
    assert _parse_nutrition(cell) == _SAMPLE


@pytest.mark.parametrize(
    "cell",
    [
        None,  # missing column (RecipeNLG)
        "",  # blank cell
        "not a list",  # unparseable
        "[1, 2, 3]",  # wrong length
        "[1, 2, 3, 4, 5, 6, 'x']",  # non-numeric element
        "[1, -2, 3, 4, 5, 6, 7]",  # negative value (corrupt)
    ],
)
def test_parse_nutrition_rejects_bad_cells(cell: object) -> None:
    """Anything that is not exactly seven non-negative numbers is rejected → OFF fallback (None)."""
    assert _parse_nutrition(cell) is None
