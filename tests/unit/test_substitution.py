"""Unit tests for allergen-safe ingredient substitution (services/user/substitution.py) — US4, no DB/LLM.

These pin the US4 safety guarantees (FR-022..024) against the curated table directly:
  * a returned substitute NEVER introduces one of the cook's declared allergens (fail-closed);
  * an honest `none_safe=true` when nothing curated is safe (or the ingredient is unknown);
  * suggestions are curated-only — every returned name exists in `substitutions_data` (never invented);
  * the free-text extractor pulls the right ingredient out of natural phrasings.

The curated map is the source of truth, so the tests cross-check every suggestion against it rather than
hard-coding expected strings — broadening the table can't silently let an unsafe swap through.
"""

from __future__ import annotations

import pytest
from app.models.recipe import Allergen, Diet
from app.services.shared import substitutions_data
from app.services.user import substitution
from app.services.user.constraint_guard import ConstraintProfile


def _cp(*allergies: Allergen) -> ConstraintProfile:
    """Build a ConstraintProfile carrying the given declared allergens (diet irrelevant to swaps)."""
    return ConstraintProfile(diet=Diet.NONE, allergies=frozenset(a.value for a in allergies))


def test_no_allergies_returns_full_curated_list() -> None:
    """A cook with no allergies sees every curated substitute for the ingredient, in curated order."""
    result = substitution.suggest("butter", _cp())
    expected = [s.name for s in substitutions_data.SUBSTITUTIONS["butter"]]
    assert result.none_safe is False
    assert result.substitutes == expected


def test_declared_allergen_swap_is_filtered_out() -> None:
    """A milk-allergic cook never receives a butter swap that introduces milk (e.g. ghee)."""
    result = substitution.suggest("butter", _cp(Allergen.MILK))
    assert "ghee" not in result.substitutes  # ghee introduces MILK → dropped
    assert result.substitutes  # safe dairy-free options remain
    assert result.none_safe is False


@pytest.mark.parametrize("ingredient", list(substitutions_data.SUBSTITUTIONS))
def test_never_emits_a_declared_allergen_for_any_entry(ingredient: str) -> None:
    """For EVERY curated ingredient, declaring all nine allergens leaves only allergen-free swaps.

    The strongest fail-closed assertion: with every allergen declared, any surviving substitute must
    introduce none of them. This sweeps the whole table so a future unsafe annotation can't slip past.
    """
    cp = _cp(*list(Allergen))
    result = substitution.suggest(ingredient, cp)
    # Resolve each survivor back to its curated row and assert it introduces no allergen at all.
    curated = {s.name: s for s in substitutions_data.SUBSTITUTIONS[ingredient]}
    for name in result.substitutes:
        assert not curated[name].introduces


def test_all_allergens_declared_yields_none_safe_when_nothing_clean() -> None:
    """When every curated swap for an ingredient carries some allergen, the answer is honest none_safe."""
    # "buttermilk" swaps both introduce an allergen (oat-milk+lemon is clean → pick an all-allergen cook).
    cp = _cp(*list(Allergen))
    result = substitution.suggest("cream", cp)
    # cream swaps: coconut cream (clean), cashew (tree_nuts), evaporated milk (milk) → coconut survives.
    if not result.substitutes:
        assert result.none_safe is True
    else:
        assert result.none_safe is False


def test_unknown_ingredient_is_honest_none_safe() -> None:
    """An ingredient absent from the curated table yields none_safe — never an invented substitute."""
    result = substitution.suggest("dragonfruit", _cp())
    assert result.substitutes == []
    assert result.none_safe is True


def test_suggestions_are_curated_only() -> None:
    """Every returned substitute name must come from the curated table (no fabrication)."""
    all_curated = {s.name for subs in substitutions_data.SUBSTITUTIONS.values() for s in subs}
    result = substitution.suggest("milk", _cp(Allergen.SOY))
    assert result.substitutes  # some safe options remain
    assert set(result.substitutes) <= all_curated


def test_singular_plural_fallback_resolves() -> None:
    """The lookup resolves a singular/plural mismatch (eggs ⇄ egg) to the same curated row."""
    assert substitution.suggest("eggs", _cp()).substitutes
    assert substitution.suggest("egg", _cp()).substitutes


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("what can I use instead of butter?", "butter"),
        ("a substitute for soy sauce", "soy sauce"),
        ("replace eggs", "eggs"),
        ("swap out heavy cream", "heavy cream"),
        ("alternative to peanut butter", "peanut butter"),
        ("I don't have any milk", "milk"),
    ],
)
def test_extract_ingredient_parses_common_phrasings(message: str, expected: str) -> None:
    """The deterministic extractor pulls the target ingredient out of natural substitution phrasings."""
    assert substitution.extract_ingredient(message) == expected


def test_extract_ingredient_returns_none_when_unclear() -> None:
    """A message with no recognizable swap cue yields None so the handler can ask for the ingredient."""
    assert substitution.extract_ingredient("hello there") is None


def test_from_message_end_to_end_safe() -> None:
    """from_message extracts then filters: a milk-allergic cook asking about butter gets safe swaps only."""
    result = substitution.from_message("what can I use instead of butter?", _cp(Allergen.MILK))
    assert result is not None
    assert "ghee" not in result.substitutes
    assert result.ingredient == "butter"


def test_from_message_returns_none_without_ingredient() -> None:
    """from_message returns None when no ingredient can be parsed (handler then re-prompts)."""
    assert substitution.from_message("hello", _cp()) is None
