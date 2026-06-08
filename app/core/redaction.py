"""Deterministic secret/PII redaction shared by logging AND tracing.

This is the single choke point both the log pipeline (app/core/logging.py) and the trace span
processor (app/infra/tracing.py) call before anything leaves the process (FR-007). In this
phase it is a deterministic stub: it masks values of known secret-ish keys and obvious token
patterns. Full Presidio-backed PII detection is wired in a later phase.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MASK = "[REDACTED]"

# Mapping keys whose VALUES are always masked, regardless of content.
_SECRET_KEY_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "key",
    "credential",
)

# Patterns of obvious secrets embedded in free text.
_TOKEN_PATTERNS = [
    # provider-style API keys, incl. hyphenated multi-segment forms (sk-proj-…, gsk-live-…)
    re.compile(r"\b(?:sk|pk|rk|gsk)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),  # bearer tokens
    re.compile(r"\bhvs\.[A-Za-z0-9._\-]+"),  # Vault service tokens
    # key=value / key: value where the key name itself looks secret
    re.compile(
        r"\b[\w\-]*(?:secret|token|password|passwd|api[_-]?key|credential)[\w\-]*\s*[=:]\s*\S+",
        re.IGNORECASE,
    ),
]


def _is_secret_key(key: str) -> bool:
    """Return True when a mapping key NAME suggests its value is a secret."""
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def redact(text: str) -> str:
    """Return text with obvious secret/token substrings replaced by the mask.

    Pure and deterministic: applies each known token pattern in turn. Non-str inputs are
    returned unchanged — callers holding structured data should use redact_mapping instead.
    """
    if not isinstance(text, str):
        return text
    out = text
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(MASK, out)
    return out


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of a mapping.

    A value is masked outright when its KEY looks secret; otherwise string values pass through
    redact() to catch tokens embedded in free text, and nested mappings are redacted
    recursively so a secret cannot hide one level down.
    """
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            redacted[key] = redact_mapping(value)
        elif _is_secret_key(str(key)):
            redacted[key] = MASK
        elif isinstance(value, str):
            redacted[key] = redact(value)
        else:
            redacted[key] = value
    return redacted
