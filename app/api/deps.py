"""Request-scoped FastAPI dependencies for the cook-facing API.

`require_profile_id` enforces the passwordless identity rule: the owner comes from the `X-Profile-ID`
header only, never the request body (constitution P6). `get_db` yields an ORM session sourced from the
app's `Database` adapter on `app.state.db`, committing on success and rolling back on error so routers
never touch the engine directly.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError


def require_profile_id(x_profile_id: str | None = Header(default=None)) -> str:
    """Extract and validate the X-Profile-ID header, returning the trimmed profile-ID.

    Raises a 400 AppError when the header is missing or blank. This is the single place cook identity
    enters the system; nothing reads an owner from a request body.
    """
    if x_profile_id is None or not x_profile_id.strip():
        raise AppError(
            "Missing or blank X-Profile-ID header.",
            status_code=400,
            code="missing_profile_id",
        )
    return x_profile_id.strip()


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
