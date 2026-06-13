"""Unit tests for parsing Food.com-style ingredient lines into structured fields (ingestion).

Food.com RAW_recipes is the preferred Kaggle source because each ingredient line carries a quantity
(unlike RecipeNLG's names-only column). This proves `ingestion.extract_ingredients` turns a
representative Food.com line into a structured `quantity` + `unit` + `name` — the shape the loader
stores and that serving-scaling (`nutrition.scale`) and per-ingredient aggregation (`nutrition.aggregate`
via `_grams`) depend on (FR-009). `raw_text` is always retained verbatim — nothing is invented.

No DB, no network — the parser is pure regex + a units whitelist.
"""

from __future__ import annotations

from ingestion import extract_ingredients


def test_count_unit_line_parses_to_quantity_unit_name() -> None:
    """'2 cloves garlic' → quantity 2, unit 'cloves', name 'garlic' (feeds the count-unit gram path)."""
    parsed = extract_ingredients.parse_line("2 cloves garlic", position=0)
    assert parsed == {
        "position": 0,
        "name": "garlic",
        "quantity": 2.0,
        "unit": "cloves",
        "raw_text": "2 cloves garlic",
    }


def test_mixed_number_and_volume_unit_parses() -> None:
    """A mixed number + volume unit ('1 1/2 cups all-purpose flour') yields quantity 1.5, unit 'cups'."""
    parsed = extract_ingredients.parse_line("1 1/2 cups all-purpose flour", position=0)
    assert parsed["quantity"] == 1.5
    assert parsed["unit"] == "cups"
    assert parsed["name"] == "all-purpose flour"


def test_fraction_quantity_with_teaspoon_unit() -> None:
    """A bare fraction ('1/2 teaspoon salt') parses to 0.5 with the teaspoon unit and a clean name."""
    parsed = extract_ingredients.parse_line("1/2 teaspoon salt", position=3)
    assert parsed["position"] == 3
    assert parsed["quantity"] == 0.5
    assert parsed["unit"] == "teaspoon"
    assert parsed["name"] == "salt"


def test_glued_quantity_and_unit_is_split() -> None:
    """A glued measure ('10ml vanilla extract') is un-glued so quantity/unit still resolve structurally."""
    parsed = extract_ingredients.parse_line("10ml vanilla extract", position=0)
    assert parsed["quantity"] == 10.0
    assert parsed["unit"] == "ml"
    assert parsed["name"] == "vanilla extract"


def test_prep_words_dropped_from_name() -> None:
    """Descriptor/prep words ('finely chopped fresh') are stripped so the name is the stable identity key."""
    parsed = extract_ingredients.parse_line("3 finely chopped fresh tomatoes", position=0)
    assert parsed["quantity"] == 3.0
    assert parsed["name"] == "tomatoes"


def test_raw_text_retained_verbatim() -> None:
    """The original line is preserved exactly in raw_text — the detail view shows it, nothing invented."""
    raw = "2 cups   Whole Milk (room temperature)"
    parsed = extract_ingredients.parse_line(raw, position=0)
    assert parsed["raw_text"] == raw


def test_empty_line_returns_none() -> None:
    """A blank/whitespace-only line is dropped (returns None) rather than producing an empty ingredient."""
    assert extract_ingredients.parse_line("   ", position=0) is None


def test_extract_assigns_sequential_positions() -> None:
    """`extract` parses a whole Food.com ingredient list, dropping blanks and re-numbering positions 0..n."""
    lines = ["2 cloves garlic", "", "1 1/2 cups flour", "1/2 teaspoon salt"]
    parsed = extract_ingredients.extract(lines)
    assert [p["position"] for p in parsed] == [0, 1, 2]
    assert [p["name"] for p in parsed] == ["garlic", "flour", "salt"]
    # Each kept line is structured enough for scaling + aggregation (quantity present).
    assert all(p["quantity"] is not None for p in parsed)
