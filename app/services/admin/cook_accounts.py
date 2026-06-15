"""Cook-account business rules (009) — admin create/list/deactivate/reactivate/reset.

The one place the admin-facing cook policy lives (Constitution III): duplicate-username (409) and weak/empty
password (422) on create and reset. There is NO last-admin guard here — cook accounts are role-less, so that
protection is purely 008's concern (admin-cooks-api.md). All DB access goes through `repo.cook_accounts`;
password primitives through `core.security`. Every mutating action emits a structlog audit line carrying the
action + actor (the operator admin) + target ONLY — never a password, hash, or token (FR-016; redaction-safe
by construction). Cooks come to exist ONLY through here (an operator admin) — there is no self-service path.
"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.core import security
from app.core.errors import AppError
from app.models.cook_account import CookAccount
from app.repo import cook_accounts as repo

log = structlog.get_logger()

# v1 password policy: a non-empty minimum length (FR-007/FR-010). Same rule on create and reset.
_MIN_PASSWORD_LENGTH = 8


def _validate_password(password: str) -> None:
    """Reject an empty or too-short password with a 422 (FR-010) — the single password-strength gate."""
    if not password or len(password) < _MIN_PASSWORD_LENGTH:
        raise AppError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.",
            status_code=422,
            code="weak_password",
        )


def create_cook(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: str | None = None,
    created_by: str | None = None,
) -> CookAccount:
    """Create a cook: reject a weak password (422) and a duplicate username (409), then bcrypt-hash.

    `display_name` defaults to the username when blank. The duplicate check runs before insert; the DB
    UNIQUE constraint is the race-safe backstop. The plaintext password is hashed here and never stored or
    logged. This is the only creation route and callers gate it to operator admins (no self-service, FR-002).
    """
    _validate_password(password)
    if repo.get_by_username(session, username) is not None:
        raise AppError("Username already taken.", status_code=409, code="cook_exists")
    cook = repo.create(
        session,
        username=username,
        display_name=display_name or username,
        password_hash=security.hash_password(password),
        created_by=created_by,
    )
    log.info("cook_account_created", action="create", actor=created_by, target=username)
    return cook


def list_cooks(session: Session) -> list[CookAccount]:
    """Return all cooks (active and disabled), newest first, for the admin Cooks listing (FR-007)."""
    return repo.list_all(session)


def deactivate(session: Session, username: str, *, actor: str | None = None) -> CookAccount:
    """Disable a cook (200); 404 for an unknown username. A disabled cook loses access on its next request."""
    cook = repo.get_by_username(session, username)
    if cook is None:
        raise AppError("No such cook.", status_code=404, code="cook_not_found")
    updated = repo.set_active(session, username, is_active=False)
    assert updated is not None  # the row existed above; set_active only returns None on a missing row
    log.info("cook_account_deactivated", action="deactivate", actor=actor, target=username)
    return updated


def reactivate(session: Session, username: str, *, actor: str | None = None) -> CookAccount:
    """Re-enable a disabled cook (200); 404 for an unknown username."""
    cook = repo.get_by_username(session, username)
    if cook is None:
        raise AppError("No such cook.", status_code=404, code="cook_not_found")
    updated = repo.set_active(session, username, is_active=True)
    assert updated is not None
    log.info("cook_account_reactivated", action="reactivate", actor=actor, target=username)
    return updated


def reset_password(
    session: Session, username: str, new_password: str, *, actor: str | None = None
) -> CookAccount:
    """Set a new password for a cook (admin-only recovery, FR-010); 422 weak, 404 unknown.

    Same <8-char rule as creation. The new bcrypt hash replaces the old one and bumps `updated_at`; existing
    cook sessions stay valid until they expire (revocation is deactivation-driven, gating-model.md).
    """
    _validate_password(new_password)
    cook = repo.get_by_username(session, username)
    if cook is None:
        raise AppError("No such cook.", status_code=404, code="cook_not_found")
    updated = repo.set_password(session, username, security.hash_password(new_password))
    assert updated is not None
    log.info("cook_account_password_reset", action="reset_password", actor=actor, target=username)
    return updated
