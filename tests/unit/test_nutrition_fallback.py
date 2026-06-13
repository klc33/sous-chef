"""Unit tests for the curated USDA nutrition fallback (ingestion).

Covers the two additive coverage-wideners that stop a recipe's nutrition from collapsing to all-zeros:
  * `_grams` resolving count / unit-less lines via per-item and per-count-unit average masses, and
  * `aggregate` falling back to curated USDA per-100g macros when Open Food Facts returns nothing.

Both must stay strictly additive — the previously-handled paths (weight/volume units, real OFF hits) are
unchanged and a genuinely unresolvable ingredient still counts as unmapped. No DB, no network: OFF is a
stub returning a fixed payload.
"""

from __future__ import annotations

from typing import Any

from ingestion import ingredient_nutrition_data as data
from ingestion import nutrition


class _StubOFF:
    """OFF stand-in returning a fixed nutriments payload for every name (or empty to force the fallback)."""

    def __init__(self, nutriments: dict[str, float] | None = None) -> None:
        """Store the per-100g payload every lookup should return; empty/None means OFF knows nothing."""
        self._nutriments = nutriments or {}

    def lookup_ingredient(self, name: str) -> dict[str, Any]:
        """Return the canned payload regardless of name (allergen_tags unused by aggregate)."""
        return {"allergen_tags": [], "nutriments": dict(self._nutriments)}


# --- _grams: count / unit-less resolution ----------------------------------------------------------


def test_grams_weight_unit_unchanged() -> None:
    """A known weight unit resolves exactly as before — the fallback never shadows the precise path."""
    assert nutrition._grams({"name": "flour", "quantity": 200, "unit": "g"}) == 200.0


def test_grams_unitless_whole_item() -> None:
    """'1 egg' (no unit) now resolves via the average per-item mass instead of returning None."""
    assert nutrition._grams({"name": "egg", "quantity": 2, "unit": None}) == 100.0


def test_grams_ingredient_specific_count_unit_beats_generic() -> None:
    """'2 cloves garlic' uses garlic's per-clove item mass (3 g), not the generic clove fallback."""
    assert nutrition._grams({"name": "garlic", "quantity": 2, "unit": "clove"}) == 6.0


def test_grams_generic_count_unit() -> None:
    """A count unit on an unmodeled item falls back to the generic per-unit mass ('3 slices' → 75 g)."""
    assert nutrition._grams({"name": "mystery loaf", "quantity": 3, "unit": "slice"}) == 75.0


def test_grams_unresolvable_still_none() -> None:
    """An unknown item with a variable/unknown count unit stays unmapped (returns None), as before."""
    assert nutrition._grams({"name": "mystery", "quantity": 1, "unit": "package"}) is None


def test_grams_no_quantity_still_none() -> None:
    """No quantity → nothing to scale → None (unchanged)."""
    assert nutrition._grams({"name": "egg", "quantity": None, "unit": None}) is None


# --- aggregate: USDA nutriment fallback ------------------------------------------------------------


def test_aggregate_uses_usda_when_off_empty() -> None:
    """With OFF empty, a curated ingredient is now mapped from the USDA table (200 g flour @ 364 kcal)."""
    out = nutrition.aggregate(
        [{"name": "flour", "quantity": 200, "unit": "g"}], _StubOFF(), basis_servings=1
    )
    assert out["unmapped_ingredient_count"] == 0
    assert out["calories"] == 728.0  # 364 kcal/100g * 2
    assert out["is_approximate"] is True  # averages are never exact


def test_aggregate_prefers_off_over_usda() -> None:
    """A real OFF hit wins; the curated fallback only fills genuine OFF gaps."""
    off = _StubOFF({"energy-kcal_100g": 500, "proteins_100g": 0, "carbohydrates_100g": 0, "fat_100g": 0})
    out = nutrition.aggregate(
        [{"name": "flour", "quantity": 100, "unit": "g"}], off, basis_servings=1
    )
    assert out["calories"] == 500.0  # OFF's value, not USDA's 364


def test_aggregate_unmapped_when_neither_source_knows() -> None:
    """An ingredient absent from both OFF and the USDA table still counts as unmapped (all-zero totals)."""
    out = nutrition.aggregate(
        [{"name": "unobtainium", "quantity": 100, "unit": "g"}], _StubOFF(), basis_servings=1
    )
    assert out["unmapped_ingredient_count"] == 1
    assert out["calories"] == 0.0
    assert out["is_approximate"] is True


def test_aggregate_count_unit_ingredient_now_mapped() -> None:
    """'2 cloves garlic' — previously unmapped (count unit) — now contributes via item mass + USDA macros."""
    out = nutrition.aggregate(
        [{"name": "garlic", "quantity": 2, "unit": "clove"}], _StubOFF(), basis_servings=1
    )
    assert out["unmapped_ingredient_count"] == 0
    assert out["calories"] > 0


# --- data-module lookups ---------------------------------------------------------------------------


def test_nutriments_lookup_plural_fallback() -> None:
    """A trailing-'s' name ('eggs') resolves to the singular curated row."""
    assert data.nutriments_per_100g("eggs") == data.NUTRIMENTS_PER_100G["egg"]


def test_item_grams_normalizes_case_and_space() -> None:
    """Lookup is robust to casing/padding (mirrors the parser's normalization)."""
    assert data.item_grams("  Onion ") == 110.0


def test_count_unit_grams_unknown_is_none() -> None:
    """A unit not modeled as a stable count unit returns None (kept deliberately unmapped)."""
    assert data.count_unit_grams("can") is None
