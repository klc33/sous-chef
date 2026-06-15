"""Operator-account business rules (008) — create/list/deactivate/reactivate/reset/authenticate/bootstrap.

The one place the account policy lives (Constitution III): duplicate-username (409), weak/empty password
(422), last-active-admin lockout guard (409), account-enumeration-resistant authentication, and the
idempotent first-admin bootstrap. All DB access goes through `repo.operator_accounts`; password primitives
through `core.security`. Every mutating action emits a structlog audit line carrying the action + actor +
target ONLY — never a password, hash, or token (FR-018; redaction-safe by construction).
"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.core import security
from app.core.errors import AppError
from app.models.operator_account import OperatorAccount
from app.repo import operator_accounts as repo

log = structlog.get_logger()

# v1 password policy: a non-empty minimum length (spec Assumptions / FR-010). Same rule on create and reset.
_MIN_PASSWORD_LENGTH = 8

# A fixed valid bcrypt hash to verify against when the username is unknown, so a missing account costs the
# same bcrypt work as a real one — login timing never reveals which usernames exist (research D9 / FR-012).
_DUMMY_PASSWORD_HASH = security.hash_password("constant-time-dummy-password-never-matches")


def _validate_password(password: str) -> None:
    """Reject an empty or too-short password with a 422 (FR-010) — the single password-strength gate."""
    if not password or len(password) < _MIN_PASSWORD_LENGTH:
        raise AppError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.",
            status_code=422,
            code="weak_password",
        )


def create_account(
    session: Session,
    *,
    username: str,
    password: str,
    role: str,
    display_name: str | None = None,
    created_by: str | None = None,
) -> OperatorAccount:
    """Create an account: reject a weak password (422) and a duplicate username (409), then bcrypt-hash.

    `display_name` defaults to the username when blank. The duplicate check runs before insert; the DB
    UNIQUE constraint is the race-safe backstop. The plaintext password is hashed here and never stored or
    logged. There is no self-service path — this is the only creation route and callers gate it to admins.
    """
    _validate_password(password)
    if repo.get_by_username(session, username) is not None:
        raise AppError("Username already taken.", status_code=409, code="user_exists")
    account = repo.create(
        session,
        username=username,
        display_name=display_name or username,
        role=role,
        password_hash=security.hash_password(password),
        created_by=created_by,
    )
    log.info("operator_account_created", action="create", actor=created_by, target=username, role=role)
    return account


def list_accounts(session: Session) -> list[OperatorAccount]:
    """Return all accounts (active and disabled), newest first, for the admin Users listing (FR-007)."""
    return repo.list_all(session)


def deactivate(session: Session, username: str, *, actor: str | None = None) -> OperatorAccount:
    """Disable an account; refuse (409) when the target is the last active admin (FR-015 / SC-006).

    Raises 404 for an unknown username. The guard fires only when the target is itself an active admin and
    no other active admin remains, so administration can never be locked out (research D8).
    """
    account = repo.get_by_username(session, username)
    if account is None:
        raise AppError("No such account.", status_code=404, code="user_not_found")
    if account.role == "admin" and account.is_active and repo.count_active_admins(session) <= 1:
        raise AppError(
            "Cannot deactivate the last active admin.",
            status_code=409,
            code="last_admin",
        )
    updated = repo.set_active(session, username, is_active=False)
    assert updated is not None  # the row existed above; set_active only returns None on a missing row
    log.info("operator_account_deactivated", action="deactivate", actor=actor, target=username)
    return updated


def reactivate(session: Session, username: str, *, actor: str | None = None) -> OperatorAccount:
    """Re-enable a disabled account (200); 404 for an unknown username."""
    account = repo.get_by_username(session, username)
    if account is None:
        raise AppError("No such account.", status_code=404, code="user_not_found")
    updated = repo.set_active(session, username, is_active=True)
    assert updated is not None
    log.info("operator_account_reactivated", action="reactivate", actor=actor, target=username)
    return updated


def reset_password(
    session: Session, username: str, new_password: str, *, actor: str | None = None
) -> OperatorAccount:
    """Set a new password for an account (admin-only recovery, FR-008a); 422 weak, 404 unknown.

    Same <8-char rule as creation. The new bcrypt hash replaces the old one and bumps `updated_at`;
    existing tokens for the account stay valid until they expire (revocation is deactivation-driven, D2).
    """
    _validate_password(new_password)
    account = repo.get_by_username(session, username)
    if account is None:
        raise AppError("No such account.", status_code=404, code="user_not_found")
    updated = repo.set_password(session, username, security.hash_password(new_password))
    assert updated is not None
    log.info("operator_account_password_reset", action="reset_password", actor=actor, target=username)
    return updated


def authenticate(session: Session, username: str, password: str) -> OperatorAccount | None:
    """Return the account on valid credentials for an ACTIVE user, else None (one generic outcome).

    Enumeration-resistant (FR-012): an unknown username still runs a bcrypt verify against a dummy hash so
    timing does not reveal whether the username exists. A correct password for a disabled account also
    returns None (a deactivated account cannot sign in). The API maps None to a single generic 401.
    """
    account = repo.get_by_username(session, username)
    if account is None:
        security.verify_password(password, _DUMMY_PASSWORD_HASH)  # equalize timing; result discarded
        return None
    if not security.verify_password(password, account.password_hash):
        return None
    if not account.is_active:
        return None
    return account


def bootstrap_admin(session: Session, *, username: str, password: str) -> OperatorAccount | None:
    """Seed the first admin from Vault creds when the table is empty; a no-op otherwise (idempotent).

    Guarantees a fresh deploy always has a way in (FR-014) without ever creating a second admin or
    resetting a changed password on restart. Returns the created account, or None when one already existed.
    """
    if repo.exists_any(session):
        return None
    account = repo.create(
        session,
        username=username,
        display_name=username,
        role="admin",
        password_hash=security.hash_password(password),
        created_by=None,
    )
    log.info(
        "operator_account_bootstrapped",
        action="bootstrap",
        actor=None,
        target=username,
        role="admin",
    )
    return account
