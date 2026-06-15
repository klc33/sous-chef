"""Admin account-management API (008, US1) — `POST/GET /admin/users` and the per-account actions.

Thin HTTP over `services/admin/operator_accounts`: every route is gated by `require_admin` (a valid JWT for
an active `admin`-role account), validates its body via the `app/schemas/operator` models, delegates the
policy (duplicate/weak-password/last-admin/404) to the service, and returns the safe `AccountView`
projection — `password_hash` never leaves the backend. There is NO self-service path: this admin-gated
surface is the only way an account is created, disabled, re-enabled, or has its password reset. The acting
admin (from `AdminAuth`) is recorded as the actor on every mutation for the audit line. Mirrors
contracts/auth-api.md; service errors already carry the right status + machine code, so the routes raise
nothing of their own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminAuth
from app.api.deps import get_db
from app.models.operator_account import OperatorAccount
from app.schemas.operator import AccountView, CreateUserRequest, ResetPasswordRequest
from app.services.admin import operator_accounts as accounts

router = APIRouter(prefix="/admin/users", tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=AccountView, status_code=201)
def create_user(
    body: CreateUserRequest,
    admin: AdminAuth,
    session: DbSession,
) -> OperatorAccount:
    """Provision a new operator account (201) — the only creation path, admin-gated (FR-009/FR-010).

    Delegates to `create_account`, which rejects a duplicate username (409) and a weak password (422) and
    bcrypt-hashes the credential. The acting admin's username is stored as `created_by` for the audit trail.
    """
    return accounts.create_account(
        session,
        username=body.username,
        password=body.password,
        role=body.role,
        display_name=body.display_name,
        created_by=admin.username,
    )


@router.get("", response_model=list[AccountView])
def list_users(
    _: AdminAuth,
    session: DbSession,
) -> list[OperatorAccount]:
    """List every account (active and disabled), newest first, for the admin Users page (FR-007)."""
    return accounts.list_accounts(session)


@router.post("/{username}/deactivate", response_model=AccountView)
def deactivate_user(
    username: str,
    admin: AdminAuth,
    session: DbSession,
) -> OperatorAccount:
    """Disable an account (200); 409 `last_admin` for the last active admin, 404 for an unknown username."""
    return accounts.deactivate(session, username, actor=admin.username)


@router.post("/{username}/reactivate", response_model=AccountView)
def reactivate_user(
    username: str,
    admin: AdminAuth,
    session: DbSession,
) -> OperatorAccount:
    """Re-enable a disabled account (200); 404 for an unknown username."""
    return accounts.reactivate(session, username, actor=admin.username)


@router.post("/{username}/reset-password", response_model=AccountView)
def reset_user_password(
    username: str,
    body: ResetPasswordRequest,
    admin: AdminAuth,
    session: DbSession,
) -> OperatorAccount:
    """Set a new password (admin-only recovery, FR-008a): 200 on success, 422 weak, 404 unknown.

    Rotates the credential only — existing tokens for the account stay valid until they expire (D2).
    """
    return accounts.reset_password(session, username, body.password, actor=admin.username)
