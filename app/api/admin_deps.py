"""Operator-auth dependencies for the admin surface — the JWT machine boundary in front of /admin/*.

Replaces the old single shared-token check (008): the dashboard now presents a per-user JWT minted at
login, so the backend knows *who* is acting and their *role*. Two dependencies gate the surface:
`require_operator` (any valid, non-expired token for an active account) guards the operational routes and
`/admin/auth/me`; `require_admin` additionally requires `role == 'admin'` and guards `/admin/users/*`. Role
is enforced here in code, never merely hidden in the UI (FR-011). A deactivated or deleted account is denied
on its very next request because every call re-loads the `sub` account and re-checks `is_active` (research
D2). Nothing reads a token from the body or query — the boundary is the `Authorization` header alone.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import VAULT_KEY_JWT_SIGNING_KEY
from app.core import security
from app.core.errors import AppError
from app.models.operator_account import OperatorAccount
from app.repo import operator_accounts as repo

# Bearer scheme prefix on the Authorization header the dashboard sends.
_BEARER_PREFIX = "Bearer "


def _unauthorized() -> AppError:
    """Build the single generic 401 used for every auth failure (no detail about *why* leaks out)."""
    return AppError(
        "Operator authentication required.",
        status_code=401,
        code="admin_unauthorized",
    )


def require_operator(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> OperatorAccount:
    """Authorize an /admin/* request by verifying its JWT and resolving the still-active account.

    Pulls the token from `Authorization: Bearer <jwt>` (401 when absent/malformed), verifies the signature
    and expiry against the Vault `JWT_SIGNING_KEY` (401 on any failure), then loads the token's `sub`
    account and requires it to still exist and be active (401 otherwise — immediate revocation of a
    deactivated account). Returns the resolved account so a route/`require_admin` can read its role.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise _unauthorized()
    token = authorization[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise _unauthorized()

    signing_key = request.app.state.vault.get(VAULT_KEY_JWT_SIGNING_KEY)
    try:
        claims = security.decode_token(token, signing_key=signing_key)
    except jwt.InvalidTokenError:
        # Covers bad signature, wrong key, and expiry alike — all collapse to one generic 401.
        raise _unauthorized() from None

    username = claims.get("sub")
    if not username:
        raise _unauthorized()
    account = repo.get_by_username(session, username)
    if account is None or not account.is_active:
        raise _unauthorized()
    return account


def require_admin(
    account: Annotated[OperatorAccount, Depends(require_operator)],
) -> OperatorAccount:
    """Gate an admin-only route: run `require_operator`, then require `role == 'admin'` (else 403).

    A valid token for a `user`-role account is authenticated but not authorized for `/admin/users/*`, so it
    gets a 403 `admin_forbidden` — the role boundary is enforced server-side, not just hidden in the UI.
    """
    if account.role != "admin":
        raise AppError(
            "Admin role required.",
            status_code=403,
            code="admin_forbidden",
        )
    return account


# Annotated aliases so endpoints declare the guard as `_: OperatorAuth` / `_: AdminAuth` without repeating
# Depends. Both resolve to the authenticated OperatorAccount, available to the route if it wants the actor.
OperatorAuth = Annotated[OperatorAccount, Depends(require_operator)]
AdminAuth = Annotated[OperatorAccount, Depends(require_admin)]
