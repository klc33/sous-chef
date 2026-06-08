"""Parse raw ingredient lines into (name, quantity, unit) — deterministic regex + a units whitelist.

Ingestion stage: turns each raw line ("<measure> <name>" from the APIs, or a free-text Kaggle line)
into the structured ingredient dict the loader stores. `raw_text` is ALWAYS retained verbatim (research
§3) — nothing is invented, and the parse stays auditable. Unparsed quantity/unit are left as None.

The parsed `name` is also *normalized* (lowercased, prep/descriptor words and stray unit words removed,
parentheticals dropped) so that "fresh minced garlic clove", "garlic cloves" and "2 cloves" collapse
toward a stable key. That normalized name is what allergen matching and the Open Food Facts lookup key
on, so cleaning it directly improves allergen recognition (fewer fail-closed exclusions) and nutrition
match rates — without ever affecting `raw_text` shown on the detail view.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

# Known measurement units (singular/abbreviated forms); plurals/periods are normalized before lookup.
_UNITS = {
    "g", "gram", "grams", "kg", "kilogram", "kilograms",
    "mg", "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds",
    "ml", "milliliter", "milliliters", "l", "liter", "liters", "litre", "litres",
    "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons",
    "cup", "cups", "pint", "pints", "quart", "quarts", "gallon", "gallons",
    "clove", "cloves", "slice", "slices", "pinch", "pinches", "dash", "dashes",
    "can", "cans", "package", "packages", "pkg", "stick", "sticks",
    "piece", "pieces", "sprig", "sprigs", "handful",
}

# Descriptor / preparation words that are not part of an ingredient's identity. Removed from the parsed
# name anywhere they appear so variants collapse to the same key (e.g. all the "garlic clove" forms).
_PREP_WORDS = {
    "fresh", "freshly", "minced", "chopped", "sliced", "diced", "crushed", "mashed",
    "ground", "grated", "shredded", "peeled", "whole", "halved", "quartered", "cubed",
    "beaten", "melted", "softened", "cooked", "raw", "dried", "frozen", "canned",
    "large", "small", "medium", "finely", "thinly", "coarsely", "roughly", "ripe",
    "boneless", "skinless", "lean", "extra", "virgin", "optional", "packed", "rinsed",
    "drained", "trimmed", "seeded", "pitted", "toasted", "roasted", "warm", "cold",
    "hot", "room", "temperature", "plus", "more", "taste", "into", "cut", "inch",
    "inches", "strip", "strips", "bunch", "bunches", "head", "stalk", "stalks",
    "and", "or", "for", "the", "a", "an",
}

# Words that, when leading the remaining name, are dropped (connector noise like "of garlic").
_LEADING_NOISE = {"of"}

# Map common unicode fraction glyphs to their decimal value.
_UNICODE_FRACTIONS = {
    "½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75,
    "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8, "⅙": 1 / 6, "⅛": 0.125,
}

# A leading quantity token: a mixed number ("1 1/2"), a fraction ("1/2"), or a decimal/integer ("2.5").
_QTY_RE = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)")

# Splits a glued quantity+unit ("2tbsp" → "2 tbsp", "10ml" → "10 ml") by inserting a space between a
# digit and an immediately following letter.
_GLUED_RE = re.compile(r"(?<=[0-9])(?=[a-zA-Z])")


def _parse_quantity(token: str) -> float | None:
    """Convert a matched quantity token (mixed/fraction/decimal) to a float, or None if it won't parse."""
    token = token.strip()
    try:
        if " " in token:  # mixed number like "1 1/2"
            whole, frac = token.split()
            return float(int(whole) + Fraction(frac))
        if "/" in token:  # bare fraction like "3/4"
            return float(Fraction(token))
        return float(token)
    except (ValueError, ZeroDivisionError):
        return None


def _strip_leading_unicode_fraction(text: str) -> tuple[float | None, str]:
    """If the line starts with a unicode fraction glyph, return its value and the remaining text."""
    if text and text[0] in _UNICODE_FRACTIONS:
        return _UNICODE_FRACTIONS[text[0]], text[1:].strip()
    return None, text


def _clean_name(text: str) -> str:
    """Normalize a candidate ingredient name: lowercase, drop parentheticals, prep words, stray units.

    Keeps only identity-bearing words so near-duplicate phrasings converge to one key. Returns an empty
    string when nothing identity-bearing is left (the caller then falls back to the unit/raw text).
    """
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # drop "(optional)", "(about 2 cups)", etc.
    text = text.replace(",", " ")
    words = [w.strip(".") for w in text.split() if w.strip(".")]
    # Remove prep/descriptor words and any stray unit words appearing inside the name.
    words = [w for w in words if w not in _PREP_WORDS and w not in _UNITS]
    # Drop leading connector noise ("of garlic" → "garlic").
    while words and words[0] in _LEADING_NOISE:
        words.pop(0)
    return " ".join(words).strip()


def parse_line(raw_text: str, position: int) -> dict[str, Any] | None:
    """Parse one raw ingredient line into {position, name, quantity, unit, raw_text}, or None if empty.

    Strategy: un-glue any "2tbsp" form, pull a leading quantity (numeric or unicode fraction), then a
    leading unit if the next word is in the whitelist; the remainder is normalized into the name. When
    nothing identity-bearing remains (e.g. the line was just "2 cloves"), the name falls back to the
    unit word so spice-style lines collapse to a stable key instead of leaking the quantity.
    """
    original = raw_text.strip()
    if not original:
        return None

    work = _GLUED_RE.sub(" ", original)
    quantity: float | None = None

    # 1) Leading quantity — try a unicode fraction first, then a numeric token.
    quantity, work = _strip_leading_unicode_fraction(work)
    if quantity is None:
        match = _QTY_RE.match(work)
        if match:
            quantity = _parse_quantity(match.group(1))
            work = work[match.end():].strip()

    # 2) Leading unit — only if the next word (normalized) is in the whitelist.
    unit: str | None = None
    parts = work.split(maxsplit=1)
    if parts:
        candidate = parts[0].lower().strip(".")
        if candidate in _UNITS:
            unit = candidate
            work = parts[1] if len(parts) > 1 else ""

    # 3) Normalize the remainder into a clean name, with sensible fallbacks.
    name = _clean_name(work)
    if not name:
        # Nothing identity-bearing left: prefer the unit word ("cloves"), else the cleaned/raw original.
        name = unit or _clean_name(original) or original

    return {
        "position": position,
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "raw_text": original,
    }


def extract(raw_ingredients: list[str]) -> list[dict[str, Any]]:
    """Parse a recipe's raw ingredient lines into ordered ingredient dicts (empty lines dropped)."""
    parsed: list[dict[str, Any]] = []
    position = 0
    for line in raw_ingredients:
        result = parse_line(line, position)
        if result is not None:
            parsed.append(result)
            position += 1
    return parsed
