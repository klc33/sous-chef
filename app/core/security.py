"""Security seam: password hashing (bcrypt) + login token issue/verify (JWT HS256) — 008.

The single in-process place credential primitives live, so the auth boundary is one tested module
(research D3/D4). `hash_password`/`verify_password` wrap bcrypt (cost ≈12, salted, slow-by-design);
`issue_token`/`decode_token` mint and verify the short-lived login JWT carrying `{sub, role, iat, exp}`.

The HS256 `signing_key` is passed IN by the caller (which reads it from Vault — `JWT_SIGNING_KEY`) rather
than imported here, so this module stays pure and unit-testable (a test can sign with one key and prove a
different/tampered key is rejected) and never reaches into Vault or app state. No secret is logged here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import bcrypt
import jwt

if TYPE_CHECKING:
    from app.models.cook_account import CookAccount
    from app.models.operator_account import OperatorAccount

# bcrypt work factor — high enough that a brute-force guess is dear, low enough that one login is fast.
_BCRYPT_ROUNDS = 12
# The one signing algorithm we mint and accept; pinning it on decode blocks an `alg: none` downgrade.
_JWT_ALGORITHM = "HS256"
# Token-domain tag carried by every cook-session JWT. `decode_cook_token` rejects any token lacking it, so
# an operator (008) token — which carries `role`, not `typ:"cook"` — can never be replayed as a cook
# session even if the keys were confused (defense in depth atop the separate COOK_SESSION_KEY, FR-013).
_COOK_TOKEN_TYPE = "cook"


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash of `password` as a UTF-8 string (never store plaintext, FR-016)."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True when `password` matches the stored bcrypt hash, False otherwise (constant-time compare).

    A malformed/invalid stored hash is treated as a non-match rather than raising, so a bad row can never
    crash the login path — it simply fails to authenticate.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def issue_token(account: OperatorAccount, *, signing_key: str, ttl_minutes: int) -> str:
    """Mint a signed JWT for a successful login: `{sub: username, role, iat, exp}`, HS256.

    `exp` is `iat + ttl_minutes` (default 8h per config) — the authoritative session clock. The token is
    self-contained and is NOT persisted server-side; authority on each request is re-derived from a valid
    signature + non-expired `exp` + the `sub` account still existing and active (research D1/D2).
    """
    now = datetime.now(UTC)
    payload = {
        "sub": account.username,
        "role": account.role,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, signing_key, algorithm=_JWT_ALGORITHM)


def decode_token(token: str, *, signing_key: str) -> dict[str, Any]:
    """Verify and decode a login JWT, returning its claims.

    Raises `jwt.InvalidTokenError` (or a subclass: `ExpiredSignatureError` for expiry, `InvalidSignatureError`
    for a wrong/tampered key) when the token is not a valid, non-expired HS256 token signed with `signing_key`.
    The caller maps any such failure to a single generic 401 (no detail about *why* leaks to the client).
    """
    return jwt.decode(token, signing_key, algorithms=[_JWT_ALGORITHM])


def issue_cook_token(cook: CookAccount, *, signing_key: str, ttl_minutes: int) -> str:
    """Mint a signed cook-session JWT for a successful cook login: `{sub: cook.id, typ: "cook", iat, exp}`.

    `sub` is the cook account **id** (the owner key), NOT the username — it is what `require_cook` resolves
    and threads downstream exactly where the profile-ID was. `exp` is `iat + ttl_minutes` (8h) — the
    authoritative session clock. Signed HS256 with the COOK_SESSION_KEY the caller reads from Vault (a
    DIFFERENT key from the operator `JWT_SIGNING_KEY`) and tagged `typ:"cook"`, so a cook session and an
    operator session can never be confused (FR-013). Not persisted server-side; authority is re-derived per
    request from a valid signature + non-expired `exp` + the `sub` account still existing and active.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": cook.id,
        "typ": _COOK_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, signing_key, algorithm=_JWT_ALGORITHM)


def decode_cook_token(token: str, *, signing_key: str) -> dict[str, Any]:
    """Verify and decode a cook-session JWT, returning its claims; reject anything not tagged `typ:"cook"`.

    Raises `jwt.InvalidTokenError` (or a subclass) when the token is not a valid, non-expired HS256 token
    signed with `signing_key`, OR when it lacks the `typ:"cook"` tag — so an operator/008 token (even one
    that somehow reached this key) is refused here rather than honored as a cook session (FR-013). The
    caller maps any failure to a single generic 401 (no detail about *why* leaks to the client).
    """
    claims = jwt.decode(token, signing_key, algorithms=[_JWT_ALGORITHM])
    if claims.get("typ") != _COOK_TOKEN_TYPE:
        raise jwt.InvalidTokenError("token is not a cook-session token")
    return claims
