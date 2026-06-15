"""Cook login + identity API (009, US1) — `POST /auth/login` and `GET /auth/me`.

`/auth/login` is the ONLY unauthenticated cook route: it hands the credentials to the cook-auth service
(which verifies them in constant time and resists username enumeration) and, on success, mints the
cook-session JWT the widget then attaches as `Authorization: Bearer` to every other cook call. Any failure
— wrong password, unknown username, OR a disabled account — collapses to one generic 401
(`auth_invalid_credentials`, FR-008), so the response never reveals which usernames exist. `/auth/me` is
`require_cook`-gated and echoes the verified cook's username, which the widget reads to render the
signed-in state. The COOK_SESSION_KEY comes from Vault and the TTL (the authoritative 8h session clock)
from config — neither is ever read from the request. Mirrors contracts/cook-auth-api.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import CookId, get_db
from app.config import VAULT_KEY_COOK_SESSION_KEY
from app.core.errors import AppError
from app.repo import cook_accounts as repo
from app.schemas.cook_auth import CookLoginRequest, CookTokenResponse
from app.services.user import cook_auth

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


@router.post("/auth/login", response_model=CookTokenResponse)
def login(body: CookLoginRequest, request: Request, session: DbSession) -> CookTokenResponse:
    """Validate cook credentials and mint a cook-session JWT — the only unauthenticated cook route.

    Delegates the credential check to `cook_auth.authenticate`, which runs a constant-time bcrypt compare
    (with a dummy verify when the username is unknown) and returns None for a wrong password, an unknown
    user, OR a deactivated account alike — all of which map here to one generic 401 so no username is
    enumerable (FR-008). On success it signs `{sub: cook.id, typ: "cook", iat, exp}` with the Vault
    COOK_SESSION_KEY, `exp = now + cook_jwt_ttl_minutes`.
    """
    cook = cook_auth.authenticate(session, body.username, body.password)
    if cook is None:
        raise AppError(
            "Invalid username or password.",
            status_code=401,
            code="auth_invalid_credentials",
        )
    signing_key = request.app.state.vault.get(VAULT_KEY_COOK_SESSION_KEY)
    ttl_minutes = request.app.state.settings.cook_jwt_ttl_minutes
    token = cook_auth.issue_session(cook, signing_key=signing_key, ttl_minutes=ttl_minutes)
    return CookTokenResponse(access_token=token, username=cook.username)


@router.get("/auth/me")
def me(cook_id: CookId, session: DbSession) -> dict[str, str]:
    """Echo the signed-in cook's username for the widget's signed-in state (`require_cook`-gated).

    `require_cook` has already verified the token and re-loaded the still-active cook, so this just resolves
    the owner-key id back to its username (the handle the widget shows) — no detail beyond identity leaks.
    """
    cook = repo.get(session, cook_id)
    # require_cook guarantees the cook exists and is active, so this is defensive only.
    if cook is None:
        raise AppError("Cook authentication required.", status_code=401, code="cook_unauthorized")
    return {"username": cook.username}
