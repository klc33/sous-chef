"""Unit tests for the security seam (app/core/security.py) — bcrypt + JWT, no DB (T013).

These pin the two credential primitives the whole auth boundary rests on: a bcrypt hash verifies its own
password (and rejects a wrong one), and a minted JWT round-trips its `{sub, role}` claims while an expired
token, a tampered token, and one verified with the wrong key are all rejected. The signing key is passed in
explicitly, so the wrong-key case proves the seam never trusts a signature it cannot verify (research D1).
"""

from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest
from app.core import security

# ≥32 bytes so PyJWT does not warn about an under-length HMAC key (prod keys are generated ≥32 bytes too).
_KEY = "unit-test-signing-key-padded-to-32-bytes-min"


def _account(username: str = "alice", role: str = "admin") -> SimpleNamespace:
    """A minimal stand-in carrying the two attributes issue_token reads (username + role)."""
    return SimpleNamespace(username=username, role=role)


def test_bcrypt_hash_verify_roundtrip() -> None:
    """A bcrypt hash verifies its own password and rejects a wrong one; the hash is not the plaintext."""
    hashed = security.hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert security.verify_password("correct horse battery", hashed) is True
    assert security.verify_password("wrong password", hashed) is False


def test_hash_is_salted_unique() -> None:
    """Hashing the same password twice yields different hashes (per-hash salt), both still verifying."""
    a = security.hash_password("same-password")
    b = security.hash_password("same-password")
    assert a != b
    assert security.verify_password("same-password", a)
    assert security.verify_password("same-password", b)


def test_issue_then_decode_carries_claims() -> None:
    """issue_token → decode_token round-trips the sub/role claims and stamps iat/exp."""
    token = security.issue_token(_account("bob", "user"), signing_key=_KEY, ttl_minutes=480)
    claims = security.decode_token(token, signing_key=_KEY)
    assert claims["sub"] == "bob"
    assert claims["role"] == "user"
    assert "iat" in claims and "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_expired_token_rejected() -> None:
    """A token whose ttl is already in the past is rejected as expired."""
    token = security.issue_token(_account(), signing_key=_KEY, ttl_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_token(token, signing_key=_KEY)


def test_wrong_key_rejected() -> None:
    """A token verified with a different signing key fails (a forged/mis-signed token is not trusted)."""
    token = security.issue_token(_account(), signing_key=_KEY, ttl_minutes=480)
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(token, signing_key="a-different-key-also-padded-to-32-bytes")


def test_tampered_token_rejected() -> None:
    """Mutating the token body invalidates the signature → InvalidTokenError."""
    token = security.issue_token(_account(), signing_key=_KEY, ttl_minutes=480)
    # Flip a character in the payload segment to break the signature without changing the structure.
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload[:-2]}{'AA' if payload[-2:] != 'AA' else 'BB'}.{sig}"
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(tampered, signing_key=_KEY)
