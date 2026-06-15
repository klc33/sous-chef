"""Cook-account data access — the ONLY place cook_accounts rows are read or written (009).

ORM/parameterized queries only (injection-safe, Constitution III). All business rules (duplicate /
weak-password / generic-failure) live in `services/admin/cook_accounts.py` + `services/user/cook_auth.py`;
this layer is pure persistence. The unique `username` index backs both `get_by_username` (the login lookup)
and the DB-level duplicate backstop; `get` resolves the opaque id (the owner key) for `require_cook`.
Mutators `flush()` in the caller's transaction (the service/route owns the commit).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cook_account import CookAccount


def get(session: Session, account_id: str) -> CookAccount | None:
    """Return the cook row for this opaque id (the owner key), or None — the `require_cook` resolve path."""
    return session.execute(
        select(CookAccount).where(CookAccount.id == account_id)
    ).scalar_one_or_none()


def get_by_username(session: Session, username: str) -> CookAccount | None:
    """Return the cook row for this username, or None when no such cook exists (the login lookup)."""
    return session.execute(
        select(CookAccount).where(CookAccount.username == username)
    ).scalar_one_or_none()


def list_all(session: Session) -> list[CookAccount]:
    """Return every cook (active and disabled), newest first — the admin Cooks listing (FR-007)."""
    rows = session.execute(
        select(CookAccount).order_by(CookAccount.created_at.desc())
    ).scalars()
    return [*rows]


def create(
    session: Session,
    *,
    username: str,
    display_name: str | None,
    password_hash: str,
    created_by: str | None,
) -> CookAccount:
    """Insert a new cook row and return it. Callers pass an already-bcrypt-hashed password.

    Relies on the UNIQUE(username) constraint as the race-safe duplicate backstop; the service checks for a
    duplicate first and surfaces a clean 409 (this layer does not translate the integrity error). The `id`
    is filled by the model's UUID4 default on flush.
    """
    cook = CookAccount(
        username=username,
        display_name=display_name,
        password_hash=password_hash,
        created_by=created_by,
    )
    session.add(cook)
    session.flush()
    return cook


def set_active(session: Session, username: str, is_active: bool) -> CookAccount | None:
    """Toggle a cook's active flag; return the updated row, or None if the username is unknown."""
    cook = get_by_username(session, username)
    if cook is None:
        return None
    cook.is_active = is_active
    session.flush()
    return cook


def set_password(session: Session, username: str, password_hash: str) -> CookAccount | None:
    """Replace a cook's password hash; return the updated row, or None if the username is unknown.

    `updated_at` is bumped by the ORM `onupdate`. Existing cook sessions stay valid until they expire — a
    reset rotates the credential, not the live session (revocation is deactivation-driven, gating-model.md).
    """
    cook = get_by_username(session, username)
    if cook is None:
        return None
    cook.password_hash = password_hash
    session.flush()
    return cook


def exists_any(session: Session) -> bool:
    """Return True when at least one cook account exists (a cheap table-non-empty probe)."""
    return session.execute(select(CookAccount.id).limit(1)).first() is not None
