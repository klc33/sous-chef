"""Request-scoped FastAPI dependencies for the cook-facing API.

`require_cook` is the cook identity gate (009): the owner key comes ONLY from a verified cook-session JWT
(`Authorization: Bearer`), never a header or body (constitution P6 + total gating). It decodes the token
with the Vault `COOK_SESSION_KEY`, rejects anything not tagged `typ:"cook"` (so an operator/008 token can
never be replayed here), then re-loads the `sub` cook and requires it to still exist and be active — so a
deactivated cook loses access on its very next request. It returns the cook account id, the same owner-key
string the routers/repos used to thread downstream. The passwordless `X-Profile-ID` gate it replaced is
fully retired (009 T027): there is no anonymous path.

`get_db` yields an ORM session from the app's `Database` adapter on `app.state.db`, committing on success
and rolling back on error so routers never touch the engine directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.config import VAULT_KEY_COOK_SESSION_KEY
from app.core import security
from app.core.errors import AppError
from app.repo import cook_accounts as repo

# Bearer scheme prefix on the Authorization header the widget sends with every cook request.
_BEARER_PREFIX = "Bearer "


def _cook_unauthorized() -> AppError:
    """Build the single generic 401 used for every cook-auth failure (no detail about *why* leaks out)."""
    return AppError(
        "Cook authentication required.",
        status_code=401,
        code="cook_unauthorized",
    )


def require_cook(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Authorize a cook request from its cook-session JWT and return the cook account id (the owner key).

    Pulls the token from `Authorization: Bearer <jwt>` (401 when absent/malformed), verifies the signature,
    expiry, and `typ:"cook"` tag against the Vault `COOK_SESSION_KEY` (401 on any failure — an operator/008
    token is rejected by the `typ` check), then loads the token's `sub` cook and requires it to still exist
    and be active (401 otherwise — immediate revocation of a deactivated cook). A legacy `X-Profile-ID`
    header carries no Bearer token, so it falls at the first step → 401 (no anonymous path, FR-001/FR-012).
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise _cook_unauthorized()
    token = authorization[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise _cook_unauthorized()

    signing_key = request.app.state.vault.get(VAULT_KEY_COOK_SESSION_KEY)
    try:
        claims = security.decode_cook_token(token, signing_key=signing_key)
    except jwt.InvalidTokenError:
        # Covers bad signature, wrong key, expiry, and a non-`cook` token alike — all collapse to one 401.
        raise _cook_unauthorized() from None

    cook_id = claims.get("sub")
    if not cook_id:
        raise _cook_unauthorized()
    cook = repo.get(session, cook_id)
    if cook is None or not cook.is_active:
        raise _cook_unauthorized()
    return cook.id


# Annotated alias so endpoints declare the cook gate as `cook_id: CookId` without repeating Depends; it
# resolves to the cook account id (the owner key) the routers thread where `profile_id` was (used in T018).
CookId = Annotated[str, Depends(require_cook)]


def get_db(request: Request) -> Iterator[Session]:
    """Yield an ORM session bound to app.state.db; commit on success, roll back on exception.

    The session is closed in all cases. Routers depend on this rather than building sessions so the
    repo layer stays the only DB-touching code and transaction handling is uniform.
    """
    session: Session = request.app.state.db.session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
