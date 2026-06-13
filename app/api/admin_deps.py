"""Operator-auth dependency for the admin surface — the machine boundary in front of /admin/*.

Every admin endpoint depends on `require_operator`, which validates a shared bearer token against the
value loaded from Vault at startup (`ADMIN_API_TOKEN`). The public cook widget never holds this token, so
it cannot reach admin (FR-029). This is deliberately the *machine* boundary (a single shared token, not an
auth system, per research R3); the *human* boundary is the Streamlit cookie login, which then sends this
token on the dashboard's behalf. The token is compared in constant time so a wrong guess leaks no timing.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, Request

from app.config import VAULT_KEY_ADMIN_API_TOKEN
from app.core.errors import AppError

# Bearer scheme prefix on the Authorization header the dashboard sends.
_BEARER_PREFIX = "Bearer "


def _unauthorized() -> AppError:
    """Build the 401 used when the bearer token is missing or malformed (no token presented)."""
    return AppError(
        "Missing or malformed Authorization bearer token.",
        status_code=401,
        code="admin_unauthorized",
    )


def require_operator(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authorize an /admin/* request by validating its bearer token against the Vault-loaded admin token.

    Pulls the presented token from the `Authorization: Bearer <token>` header (401 when absent/malformed),
    then compares it in constant time to the `ADMIN_API_TOKEN` Vault loaded at startup (available on
    `app.state.vault`). A mismatch is 403 (a token was presented but it is not the operator's); a match
    returns None so the endpoint proceeds. Nothing here reads a token from the body or query — the boundary
    is the header alone.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise _unauthorized()
    presented = authorization[len(_BEARER_PREFIX) :].strip()
    if not presented:
        raise _unauthorized()

    expected = request.app.state.vault.get(VAULT_KEY_ADMIN_API_TOKEN)
    # Constant-time compare so a near-miss token cannot be discovered by response timing.
    if not secrets.compare_digest(presented, expected):
        raise AppError(
            "Admin token is not authorized.",
            status_code=403,
            code="admin_forbidden",
        )


# Annotated alias so endpoints declare the guard as `_: OperatorAuth` without repeating Depends.
OperatorAuth = Annotated[None, Depends(require_operator)]
