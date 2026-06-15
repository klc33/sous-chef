"""Unit tests for ingestion allergen + diet derivation (ingestion/allergens.py).

These pin the T017a fixes that feed THE WALL's diet flags:
  * animal allergen TAGS (incl. Open Food Facts-supplied milk/eggs/fish) fail the matching diet closed,
  * OFF false positives on trusted whole foods (garlic → "garlic bread" → milk) are suppressed,
  * meat cuts that carry no top-9 allergen (e.g. oxtail) are detected by name,
  * uncertain allergen detection forces every diet flag False (fail-closed).

Pure Python — a tiny in-memory fake stands in for the OFF adapter, so no network or cache is touched.
"""

from __future__ import annotations

from typing import Any

from app.models.recipe import Allergen
from ingestion.allergens import analyze, derive_diet_flags


class FakeOFF:
    """Minimal Open Food Facts stand-in: returns the allergen tags mapped to each ingredient name.

    `tags_by_name` maps a lowercase ingredient name to the OFF `allergen_tags` it should report (already
    language-prefix-stripped, e.g. "milk"); any name not in the map reports a recognized-but-clean product.
    """

    def __init__(self, tags_by_name: dict[str, list[str]]) -> None:
        self._tags = tags_by_name

    def lookup_ingredient(self, name: str) -> dict[str, Any]:
        """Return the canned OFF payload for an ingredient; non-empty nutriments mark it as recognized."""
        return {"allergen_tags": self._tags.get(name.lower(), []), "nutriments": {"proteins_100g": 1.0}}


def _ings(*names: str) -> list[dict[str, Any]]:
    """Build the ingredient-dict list `analyze` consumes from a sequence of names."""
    return [{"name": n} for n in names]


def test_off_only_milk_tag_fails_vegan_closed() -> None:
    """An OFF-supplied milk tag on a non-trusted ingredient adds the allergen AND drops vegan (the bug)."""
    off = FakeOFF({"dark chocolate": ["milk"]})
    result = analyze(_ings("dark chocolate", "sugar"), off=off)
    assert "milk" in result["allergens"]
    assert result["is_vegan"] is False  # milk present → not vegan
    assert result["is_vegetarian"] is True  # milk is vegetarian-compatible


def test_trusted_whole_food_suppresses_off_false_positive() -> None:
    """A milk tag OFF wrongly attaches to garlic is ignored, so a plant dish stays clean and vegan."""
    off = FakeOFF({"garlic": ["milk"], "salt": ["milk"]})
    result = analyze(_ings("garlic", "salt", "tomato"), off=off)
    assert result["allergens"] == []
    assert result["is_vegan"] is True
    assert result["is_vegetarian"] is True


def test_oxtail_detected_as_meat() -> None:
    """Oxtail carries no top-9 allergen but is meat — it must fail vegetarian/vegan/pescatarian."""
    off = FakeOFF({})
    result = analyze(_ings("oxtail", "onion", "garlic"), off=off)
    assert result["is_vegetarian"] is False
    assert result["is_vegan"] is False
    assert result["is_pescatarian"] is False


def test_keyword_dairy_fails_vegan_but_stays_vegetarian() -> None:
    """A dairy keyword (butter) marks milk, fails vegan, but remains vegetarian."""
    off = FakeOFF({})
    result = analyze(_ings("butter", "flour", "sugar"), off=off)
    assert "milk" in result["allergens"]
    assert result["is_vegan"] is False
    assert result["is_vegetarian"] is True


def test_uncertain_detection_forces_diet_flags_false() -> None:
    """An unrecognized ingredient loses certainty, which fails every diet closed even if it looks vegan."""
    result = analyze(_ings("zorblax extract"), off=None)
    assert result["allergen_certain"] is False
    assert result["is_vegan"] is False
    assert result["is_vegetarian"] is False
    assert result["is_pescatarian"] is False


def test_derive_diet_flags_seafood_tag_is_pescatarian_not_vegetarian() -> None:
    """A fish allergen tag makes a recipe pescatarian-compatible but not vegetarian/vegan."""
    flags = derive_diet_flags([("salmon fillet", {Allergen.FISH})], certain=True)
    assert flags["is_pescatarian"] is True
    assert flags["is_vegetarian"] is False
    assert flags["is_vegan"] is False


def test_uncommon_fish_species_detected_as_fish() -> None:
    """Species the original short list missed — orange roughy, pilchards — must tag fish, dropping vegetarian.

    These are the live wall-violation cases (a vegetarian was shown "orange roughy"): the fish keyword
    list was too small, so neither produced a fish tag and both were wrongly flagged vegetarian. "orange
    roughy" also defeated the fail-closed certainty check because "orange" is a known-safe substring.
    """
    off = FakeOFF({})
    for species in ("orange roughy", "pilchards", "swordfish", "monkfish"):
        result = analyze(_ings(species, "onion", "garlic"), off=off)
        assert "fish" in result["allergens"], species
        assert result["is_vegetarian"] is False, species
        assert result["is_pescatarian"] is True, species  # fish is pescatarian-compatible


def test_lard_is_meat_but_does_not_flag_collard_greens() -> None:
    """Lard (animal fat) fails vegetarian/pescatarian, matched as a WHOLE word so "collard" stays safe.

    `lard` is a substring of "collard greens" (a vegetable), so it must be boundary-matched: a recipe
    with lard is non-vegetarian and non-pescatarian, while a collard-greens dish remains vegetarian.
    """
    off = FakeOFF({})
    lard = analyze(_ings("lard", "flour", "sugar"), off=off)
    assert lard["is_vegetarian"] is False
    assert lard["is_pescatarian"] is False

    collard = analyze(_ings("collard greens", "onion", "garlic"), off=off)
    assert collard["is_vegetarian"] is True
    assert collard["is_pescatarian"] is True


def test_carp_substring_does_not_mistag_mascarpone() -> None:
    """The fish list deliberately excludes "carp" — "mascarpone" must not be tagged fish or lose vegetarian."""
    off = FakeOFF({})
    result = analyze(_ings("mascarpone", "sugar", "flour"), off=off)
    assert "fish" not in result["allergens"]
    assert result["is_vegetarian"] is True
