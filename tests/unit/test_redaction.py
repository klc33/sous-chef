"""Redaction gate (US3 / FR-007): secrets must never survive as cleartext.

These tests feed realistic secrets and tokens through redact()/redact_mapping() — the single
choke point both logging and tracing call before anything leaves the process — and assert the
secret material is gone. This is the unit half of the "no secret reaches logs/traces" guarantee.
"""

from __future__ import annotations

import pytest
from app.core.redaction import MASK, redact, redact_mapping

# Secrets shaped like the real things app/core/redaction.py is meant to catch.
SECRETS = [
    "sk-ABCDEF0123456789abcdef",  # provider-style API key
    "gsk-live-0123456789ABCDEFwxyz",  # Groq-style key
    "Bearer eyJ0eXAiOiJKV1Qi.payload.sig",  # bearer token
    "hvs.CAESIJfakeVaultServiceToken123",  # Vault service token
]


@pytest.mark.parametrize("secret", SECRETS)
def test_redact_masks_tokens_embedded_in_text(secret: str) -> None:
    """A secret sitting inside otherwise-normal prose is replaced and never leaks verbatim."""
    line = f"calling provider with {secret} now"
    out = redact(line)
    assert secret not in out
    assert MASK in out


def test_redact_masks_secret_keyvalue_pairs() -> None:
    """key=value / key: value whose key name looks secret has its value masked."""
    for line in ("api_key=supersecretvalue", "password: hunter2", "client_secret = abc123"):
        out = redact(line)
        assert "supersecretvalue" not in out
        assert "hunter2" not in out
        assert "abc123" not in out
        assert MASK in out


def test_redact_leaves_innocuous_text_untouched() -> None:
    """Plain text with no secret pattern passes through unchanged (no over-masking)."""
    line = "the postgres connection is healthy and redis answered the ping"
    assert redact(line) == line


def test_redact_returns_non_str_unchanged() -> None:
    """Non-string inputs are returned as-is; structured data is the caller's job via mapping."""
    assert redact(12345) == 12345  # type: ignore[arg-type]
    assert redact(None) is None  # type: ignore[arg-type]


def test_redact_mapping_masks_values_of_secret_keys() -> None:
    """A value is masked outright when its KEY name looks secret, regardless of its content."""
    mapping = {
        "vault_token": "root",
        "api_key": "sk-plainlookingvalue",
        "authorization": "Bearer xyz",
        "password": "p",
    }
    out = redact_mapping(mapping)
    for key, original in mapping.items():
        assert out[key] == MASK, key
        assert original not in str(out[key])


def test_redact_mapping_scrubs_secrets_in_nonsecret_keys() -> None:
    """A token hiding in an innocently-named string field is still caught by redact()."""
    out = redact_mapping({"event": "auth ok with sk-ABCDEF0123456789abcdef"})
    assert "sk-ABCDEF0123456789abcdef" not in out["event"]
    assert MASK in out["event"]


def test_redact_mapping_recurses_into_nested_mappings() -> None:
    """A secret one level down cannot hide — nested mappings are redacted recursively."""
    out = redact_mapping({"outer": {"db_password": "letmein", "host": "postgres"}})
    assert out["outer"]["db_password"] == MASK
    assert out["outer"]["host"] == "postgres"


def test_redact_mapping_preserves_nonsecret_values() -> None:
    """Non-secret keys with non-secret values come through untouched and types are preserved."""
    out = redact_mapping({"status": "ok", "count": 3, "ratio": 1.5})
    assert out == {"status": "ok", "count": 3, "ratio": 1.5}
