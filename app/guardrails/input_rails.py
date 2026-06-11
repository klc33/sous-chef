"""Input rail — screen the cook's untrusted message BEFORE it reaches the router (Constitution VI).

`screen(message)` returns a `GuardrailDecision`. The rail is a fast, DETERMINISTIC screen (regex/keyword
patterns, no model) so the red-team gate is provable and reproducible (research §11). It enforces two
classes of manipulation:

  * **Allergen/diet-override** ("ignore my peanut allergy", "override my diet") → REFUSE the whole turn.
    Honoring even the "safe" remainder of such a request would mean surfacing against a declared allergy,
    so it is inseparable from an unsafe ask. (The wall makes this structurally impossible past retrieval
    too — this is defense in depth.)
  * **Injection / jailbreak / role-override / system-prompt-leak** ("ignore previous instructions",
    "you are now…", "reveal your system prompt") → strip the offending fragment. If a meaningful safe
    remainder survives (an injection embedded in an otherwise-valid cooking request, FR-033), ALLOW the
    cleaned remainder; if nothing safe is left, REFUSE.

Allow-by-default otherwise: a message matching no pattern passes straight through unchanged.
"""

from __future__ import annotations

import re

from app.guardrails.decision import GuardrailDecision

# The safe, non-leaking message returned on any refusal — never echoes the probe, never reveals internals.
_SAFE_REFUSAL = (
    "I can't help with that — I won't ignore your safety settings or my own instructions. "
    "I can find recipes, plan meals, answer nutrition questions, or suggest allergen-safe swaps."
)

# Allergen/diet-override phrasing — an attempt to talk the assistant out of a declared allergy or diet.
# These REFUSE outright (no safe remainder is served): the request is fundamentally an unsafe ask.
_OVERRIDE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\s+(?:my|the)\s+(?:\w+\s+){0,3}?(?:allerg(?:y|ies)|diet|dietary|restrictions?)",
        r"\bforget\s+(?:my|the)\s+(?:\w+\s+){0,3}?(?:allerg(?:y|ies)|diet|dietary|restrictions?)",
        r"\bdespite\s+my\s+(?:\w+\s+){0,3}?(?:allerg(?:y|ies)|diet)",
        r"\boverride\s+(?:my|the)\s+(?:allerg(?:y|ies)|diet|dietary|restrictions?)",
        r"\bbypass\s+(?:my|the)\s+(?:\w+\s+){0,3}?(?:allerg(?:y|ies)|diet|wall|safety|restrictions?)",
        r"\b(?:don'?t|do\s+not)\s+(?:apply|enforce|check|worry\s+about|respect)\s+(?:my|the)\s+"
        r"(?:allerg(?:y|ies)|diet|dietary|restrictions?)",
        r"\beven\s+(?:if|though)\s+(?:i'?m|i\s+am)\s+allergic",
        r"\bi\s+(?:don'?t|do\s+not)\s+(?:really|actually)\s+have\s+(?:a|an|any)?\s*\w*\s*allerg",
    )
)

# Prompt-injection / jailbreak / role-override / system-prompt-leak phrasing. These get the fragment
# stripped so a safe remainder of an otherwise-valid request can still be served (FR-033).
_INJECTION_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|earlier|above|preceding|"
        r"foregoing)\s+(?:instructions?|prompts?|messages?|rules?|directions?)",
        r"\bdisregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|earlier|above)\s+"
        r"(?:instructions?|prompts?|rules?)",
        r"\bforget\s+(?:everything|all|your|the|the\s+above|previous|prior)\b[^.?!]*"
        r"(?:instructions?|rules?|prompt|told)",
        r"\byou\s+are\s+now\b",
        r"\byou'?re\s+now\b",
        r"\bpretend\s+(?:to\s+be|you'?re|that\s+you|you\s+are)\b",
        r"\brole-?play\s+as\b",
        r"\bsystem\s+prompt\b",
        r"\b(?:reveal|show|print|repeat|tell\s+me|give\s+me|output|display)\b[^.?!]*"
        r"\byour\s+(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?)",
        r"\brepeat\s+(?:the\s+)?(?:words?|text|everything)\s+above",
        r"\bdeveloper\s+mode\b",
        r"\bjail\s*break\b",
        r"\bbypass\s+(?:your|the|all)\s+(?:safety|guardrails?|rules?|restrictions?|filters?)",
        r"\bignore\s+your\s+(?:safety\s+)?(?:guidelines?|rules?|restrictions?|programming)",
        r"\boverride\s+your\s+(?:safety|instructions?|rules?|programming)",
        r"\bact\s+as\s+(?:a\s+|an\s+)?(?:dan|jailbroken|unrestricted|unfiltered|different\s+ai)\b",
    )
)

# Split a message into sentence-like segments so an injected sentence can be dropped while the rest of a
# multi-sentence request survives. Splits on terminators (. ! ?) and newlines — the natural seams a
# pasted "valid request. injected instruction." attack falls along.
_SEGMENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _matches_any(message: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Return True when any compiled pattern is found anywhere in the message (case-insensitive)."""
    return any(p.search(message) for p in patterns)


def _strip_injection(message: str) -> str:
    """Return the message with any segment that matches an injection pattern removed.

    Splits the message into sentence-like segments and keeps only those that carry no injection phrasing,
    so an attack pasted as its own sentence is dropped while a legitimate cooking sentence beside it
    survives. Rejoined with single spaces; the leftover is what `screen` then judges for safe remainder.
    """
    segments = _SEGMENT_SPLIT.split(message)
    kept = [seg for seg in segments if seg.strip() and not _matches_any(seg, _INJECTION_PATTERNS)]
    return " ".join(s.strip() for s in kept).strip()


def _has_meaningful_remainder(text: str) -> bool:
    """Return True when the stripped text still reads like a real request, not leftover filler.

    A safe remainder must contain at least TWO alphabetic word tokens (2+ chars each): a genuine embedded
    request ("find me a vegan pasta recipe") clears this, while a jailbreak's trailing single-word filler
    ("Confirm.", "Thanks") does not — so a message that was essentially ONLY an injection is refused rather
    than served as a hollow query. Punctuation and whitespace never count.
    """
    return len(re.findall(r"[A-Za-z]{2,}", text)) >= 2


def screen(message: str) -> GuardrailDecision:
    """Screen an inbound message and return the rail's verdict (allow / allow-cleaned / refuse).

    Order matters: an allergen/diet-override is checked FIRST and refuses the whole turn, because its safe
    remainder cannot be separated from the unsafe ask. Otherwise, if the message carries an injection /
    jailbreak / role-override / prompt-leak fragment, that fragment is stripped; a surviving meaningful
    remainder is ALLOWED as a cleaned message (FR-033) and an empty remainder is REFUSED. A message
    matching nothing passes through unchanged. The endpoint short-circuits on `refuse` and routes
    `sanitized_message` when present.
    """
    if _matches_any(message, _OVERRIDE_PATTERNS):
        return GuardrailDecision(stage="input", action="refuse", reason=_SAFE_REFUSAL)

    if _matches_any(message, _INJECTION_PATTERNS):
        cleaned = _strip_injection(message)
        if _has_meaningful_remainder(cleaned):
            # The injected sentence is gone; serve the legitimate remainder under the cleaned text.
            return GuardrailDecision(stage="input", action="allow", sanitized_message=cleaned)
        return GuardrailDecision(stage="input", action="refuse", reason=_SAFE_REFUSAL)

    return GuardrailDecision(stage="input", action="allow")
