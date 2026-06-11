"""Allergen-safe ingredient substitution — curated lookup, fail-closed allergen filter (US4).

A cook asks "what can I use instead of X?" and gets plausible replacements that NEVER introduce an
allergen they declared. The guarantee is the same fail-closed stance as the wall: suggestions come only
from the curated table (`services/shared/substitutions_data.py`), and any substitute whose introduced
allergens intersect the cook's declared allergies is dropped (FR-022..024). When nothing curated is safe
— or the ingredient isn't in the table at all — the result is an honest `none_safe=true`, never an
invented swap.

`suggest(ingredient, cp)` is the core used by the agent's `substitute_ingredient` tool (which already has
a clean ingredient). `from_message(message, cp)` adds a small deterministic extractor for the workflow
route, pulling the target ingredient out of free text like "what can I use instead of butter?".
"""

from __future__ import annotations

import re

from app.schemas.chat import SubstitutionResult
from app.services.shared import substitutions_data
from app.services.user.constraint_guard import ConstraintProfile

__all__ = ["suggest", "from_message", "extract_ingredient"]

# Phrasings that name the target ingredient after a "swap" cue. First capture group is the ingredient;
# we stop at punctuation / trailing question words so "instead of butter?" yields just "butter".
_EXTRACT_PATTERNS = [
    re.compile(r"instead of\s+(.+?)[\s]*[?.!]*$", re.IGNORECASE),
    re.compile(r"substitut\w*\s+for\s+(.+?)[\s]*[?.!]*$", re.IGNORECASE),
    re.compile(r"replace(?:ment for)?\s+(.+?)[\s]*[?.!]*$", re.IGNORECASE),
    re.compile(r"swap\s+(?:out\s+)?(.+?)[\s]*[?.!]*$", re.IGNORECASE),
    re.compile(r"alternative\s+(?:to|for)\s+(.+?)[\s]*[?.!]*$", re.IGNORECASE),
    re.compile(r"don't have\s+(?:any\s+)?(.+?)[\s]*[?.!]*$", re.IGNORECASE),
]


def suggest(ingredient: str, cp: ConstraintProfile) -> SubstitutionResult:
    """Return curated substitutes for `ingredient` with every declared-allergen swap removed (fail-closed).

    Looks the ingredient up in the curated map; an unknown ingredient yields `none_safe=true` (no curated
    data → no invented suggestion). For a known ingredient, drops any substitute whose introduced allergens
    intersect the cook's declared allergies, preserving the curated preference order. If the filter leaves
    nothing, the answer is an honest `none_safe=true` — the cook is never handed an unsafe or fabricated
    swap.
    """
    curated = substitutions_data.lookup(ingredient)
    if curated is None:
        # No curated entry — be honest rather than invent a substitute (golden rule #2).
        return SubstitutionResult(ingredient=ingredient, substitutes=[], none_safe=True)
    # Fail-closed: keep only swaps that introduce none of the cook's declared allergens.
    safe = [
        sub.name
        for sub in curated
        if not (cp.allergies & {a.value for a in sub.introduces})
    ]
    return SubstitutionResult(
        ingredient=ingredient,
        substitutes=safe,
        none_safe=not safe,
    )


def extract_ingredient(message: str) -> str | None:
    """Pull the target ingredient out of a free-text substitution request, or None if unclear.

    Tries each "swap cue" pattern (instead of / substitute for / replace / swap / alternative to /
    don't have) and returns the first captured ingredient, trimmed of trailing filler ("any", articles).
    A deterministic parse keeps the workflow path cheap and grounded; an unparseable message returns None
    so the handler can ask the cook to name the ingredient rather than guess.
    """
    for pattern in _EXTRACT_PATTERNS:
        match = pattern.search(message)
        if match:
            ingredient = match.group(1).strip()
            # Strip a leading article/quantifier the cue patterns may have left attached.
            ingredient = re.sub(r"^(a|an|the|some|any)\s+", "", ingredient, flags=re.IGNORECASE)
            if ingredient:
                return ingredient
    return None


def from_message(message: str, cp: ConstraintProfile) -> SubstitutionResult | None:
    """Extract the ingredient from a free-text request then delegate to `suggest`; None if no ingredient.

    The workflow's substitution route entry point: parses the cook's message for the target ingredient and
    runs the curated, allergen-safe lookup. Returns None only when no ingredient could be identified, so
    the handler can ask the cook to name it instead of substituting nothing.
    """
    ingredient = extract_ingredient(message)
    if ingredient is None:
        return None
    return suggest(ingredient, cp)
