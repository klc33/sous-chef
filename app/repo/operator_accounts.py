"""Operator-account data access — the ONLY place operator_accounts rows are read or written.

ORM/parameterized queries only (injection-safe, Constitution III). All business rules (duplicate/weak
password/last-admin/generic-failure) live in `services/admin/operator_accounts.py`; this layer is pure
persistence. The unique `username` index backs both `get_by_username` (the login lookup) and the DB-level
duplicate backstop. Mutators `flush()` in the caller's transaction (the service/route owns the commit).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.operator_account import OperatorAccount


def get_by_username(session: Session, username: str) -> OperatorAccount | None:
    """Return the account row for this username, or None when no such account exists."""
    return session.execute(
        select(OperatorAccount).where(OperatorAccount.username == username)
    ).scalar_one_or_none()


def list_all(session: Session) -> list[OperatorAccount]:
    """Return every account (active and disabled), newest first — the admin Users listing (FR-007)."""
    rows = session.execute(
        select(OperatorAccount).order_by(OperatorAccount.created_at.desc())
    ).scalars()
    return [*rows]


def create(
    session: Session,
    *,
    username: str,
    display_name: str | None,
    role: str,
    password_hash: str,
    created_by: str | None,
) -> OperatorAccount:
    """Insert a new account row and return it. Callers pass an already-bcrypt-hashed password.

    Relies on the UNIQUE(username) constraint as the race-safe duplicate backstop; the service checks for
    a duplicate first and surfaces a clean 409 (this layer does not translate the integrity error).
    """
    account = OperatorAccount(
        username=username,
        display_name=display_name,
        role=role,
        password_hash=password_hash,
        created_by=created_by,
    )
    session.add(account)
    session.flush()
    return account


def set_active(session: Session, username: str, is_active: bool) -> OperatorAccount | None:
    """Toggle an account's active flag; return the updated row, or None if the username is unknown."""
    account = get_by_username(session, username)
    if account is None:
        return None
    account.is_active = is_active
    session.flush()
    return account


def set_password(session: Session, username: str, password_hash: str) -> OperatorAccount | None:
    """Replace an account's password hash; return the updated row, or None if the username is unknown.

    `updated_at` is bumped by the ORM `onupdate`. Existing tokens stay valid until they expire — a reset
    rotates the credential, not the live session (research D2).
    """
    account = get_by_username(session, username)
    if account is None:
        return None
    account.password_hash = password_hash
    session.flush()
    return account


def count_active_admins(session: Session) -> int:
    """Return how many active admin accounts exist — the input to the last-admin lockout guard (FR-015)."""
    return session.execute(
        select(func.count())
        .select_from(OperatorAccount)
        .where(OperatorAccount.role == "admin", OperatorAccount.is_active.is_(True))
    ).scalar_one()


def exists_any(session: Session) -> bool:
    """Return True when at least one account exists — the idempotency check for bootstrap (FR-014)."""
    return (
        session.execute(select(OperatorAccount.id).limit(1)).first() is not None
    )
