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


def test_title_backstops_meat_omitted_from_ingredients() -> None:
    """A meat named only in the TITLE drops the meat-bearing diets even when the ingredient rows look veg.

    The live wall-violation case: sources like "Cubano pork belly" / "hawaiian chicken salad" list only the
    rub or the dressing, so the protein never reaches the ingredient-based detector and the recipe wrongly
    read as vegetarian. The title is the fail-closed backstop.
    """
    off = FakeOFF({})
    pork = analyze(_ings("olive oil", "garlic", "cumin", "oregano"), off=off, title="Cubano pork belly")
    assert pork["is_vegetarian"] is False
    assert pork["is_vegan"] is False
    assert pork["is_pescatarian"] is False  # pork is meat → not pescatarian either

    chicken = analyze(_ings("sour cream", "mayonnaise", "green onion"), off=off,
                      title="hawaiian chicken salad appetizer")
    assert chicken["is_vegetarian"] is False
    assert chicken["is_pescatarian"] is False


def test_broiler_synonym_detected_as_poultry() -> None:
    """"broiler" is a chicken — the lexicon gap that let "evil chicken" read as vegan/vegetarian."""
    off = FakeOFF({})
    result = analyze(_ings("broiler", "soy sauce", "ginger", "garlic"), off=off, title="evil chicken")
    assert result["is_vegetarian"] is False
    assert result["is_vegan"] is False
    assert result["is_pescatarian"] is False


def test_title_fish_drops_vegetarian_but_keeps_pescatarian() -> None:
    """A fish word in the title fails vegetarian/vegan closed but leaves pescatarian (seafood) intact."""
    off = FakeOFF({})
    result = analyze(_ings("buttermilk", "egg", "flour"), off=off, title="chicken fried fish fingers")
    # "chicken" in the title also makes it non-pescatarian, so probe a plain-fish title for the seafood rule:
    plain = analyze(_ings("buttermilk", "egg", "flour"), off=off, title="crispy fish fingers")
    assert result["is_vegetarian"] is False
    assert plain["is_vegetarian"] is False
    assert plain["is_pescatarian"] is True  # fish is pescatarian-compatible


def test_meat_free_title_marker_is_exempt() -> None:
    """A deliberately meat-free dish named after meat keeps its veg flags (the title backstop is skipped).

    "meatless" / "meat-free" contain the "meat" substring, so the exemption must be checked first or these
    would flag themselves; a "vegetarian sausage" with veg ingredients must stay vegetarian.
    """
    off = FakeOFF({})
    for title in ("Meatless Monday chili", "vegetarian sausage rolls", "mock duck stir fry"):
        result = analyze(_ings("onion", "garlic", "tomato", "bean"), off=off, title=title)
        assert result["is_vegetarian"] is True, title


def test_derive_diet_flags_title_only_meat_overrides() -> None:
    """Direct `derive_diet_flags` call: a meat title overrides otherwise-clean ingredients (fail-closed)."""
    flags = derive_diet_flags([("rub", set()), ("oil", set())], certain=True, title="slow beef brisket")
    assert flags["is_vegetarian"] is False
    assert flags["is_pescatarian"] is False
