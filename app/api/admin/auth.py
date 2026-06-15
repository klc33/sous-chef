"""Operator login + identity API (008, US2) — `POST /admin/auth/login` and `GET /admin/auth/me`.

`/admin/auth/login` is the ONLY unauthenticated `/admin` route: it hands the credential to the account
service (which checks it in constant time and resists username enumeration) and, on success, mints the
per-user JWT the dashboard then attaches as `Authorization: Bearer` to every other `/admin` call. Any
failure — wrong password, unknown username, OR a disabled account — collapses to one generic 401
(`auth_invalid_credentials`, FR-012), so the response never reveals which usernames exist. `/admin/auth/me`
is `require_operator`-gated and simply echoes the verified token's identity, which the dashboard reads to
decide whether to show the admin-only Users page. The JWT signing key comes from Vault and the TTL (the
authoritative 8h session clock) from config — neither is ever read from the request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.admin_deps import OperatorAuth
from app.api.deps import get_db
from app.config import VAULT_KEY_JWT_SIGNING_KEY
from app.core import security
from app.core.errors import AppError
from app.schemas.operator import LoginRequest, TokenResponse
from app.services.admin import operator_accounts as accounts

router = APIRouter(prefix="/admin/auth", tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, session: DbSession) -> TokenResponse:
    """Validate credentials and mint a JWT — the only unauthenticated `/admin` route; generic 401 on failure.

    Delegates the credential check to `authenticate`, which runs a constant-time bcrypt compare (with a
    dummy verify when the username is unknown) and returns None for a wrong password, an unknown user, OR a
    deactivated account alike — all of which map here to one generic 401 so no username is enumerable
    (FR-012). On success it signs `{sub, role, iat, exp}` with the Vault key, `exp = now + jwt_ttl_minutes`.
    """
    account = accounts.authenticate(session, body.username, body.password)
    if account is None:
        raise AppError(
            "Invalid username or password.",
            status_code=401,
            code="auth_invalid_credentials",
        )
    signing_key = request.app.state.vault.get(VAULT_KEY_JWT_SIGNING_KEY)
    ttl_minutes = request.app.state.settings.jwt_ttl_minutes
    token = security.issue_token(account, signing_key=signing_key, ttl_minutes=ttl_minutes)
    return TokenResponse(access_token=token, role=account.role, username=account.username)


@router.get("/me")
def me(operator: OperatorAuth) -> dict[str, str]:
    """Echo the caller's verified identity (`{username, role}`) for the dashboard's role-aware UI.

    `require_operator` has already verified the JWT and re-loaded the still-active account, so this just
    reflects the resolved identity back — the dashboard uses `role` to decide whether to show the Users page.
    """
    return {"username": operator.username, "role": operator.role}
