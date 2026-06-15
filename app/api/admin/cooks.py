"""Admin cook-management API (009, US2) — `POST/GET /admin/cooks` and the per-account actions.

Thin HTTP over `services/admin/cook_accounts`: every route is gated by 008's `require_admin` (a valid JWT
for an active `admin`-role operator), validates its body via the `app/schemas/cook_auth` models, delegates
the policy (duplicate-username 409 / weak-password 422 / 404) to the service, and returns the safe
`CookView` projection — `password_hash` and the opaque `id` never leave the backend. There is NO self-service
path: this admin-gated surface is the only way a cook account is created, disabled, re-enabled, or reset
(FR-002/FR-003). The acting admin (from `AdminAuth`) is recorded as the actor on every mutation for the audit
line. Mirrors contracts/admin-cooks-api.md; service errors already carry the right status + machine code, so
the routes raise nothing of their own. Unlike `/admin/users`, there is no last-admin guard — cook accounts
are role-less.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminAuth
from app.api.deps import get_db
from app.models.cook_account import CookAccount
from app.schemas.cook_auth import CookView, CreateCookRequest, ResetCookPasswordRequest
from app.services.admin import cook_accounts as cooks

router = APIRouter(prefix="/admin/cooks", tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=CookView, status_code=201)
def create_cook(
    body: CreateCookRequest,
    admin: AdminAuth,
    session: DbSession,
) -> CookAccount:
    """Provision a new cook account (201) — the only creation path, admin-gated (FR-002/FR-003).

    Delegates to `create_cook`, which rejects a duplicate username (409) and a weak password (422) and
    bcrypt-hashes the credential. The acting admin's username is stored as `created_by` for the audit trail.
    """
    return cooks.create_cook(
        session,
        username=body.username,
        password=body.password,
        display_name=body.display_name,
        created_by=admin.username,
    )


@router.get("", response_model=list[CookView])
def list_cooks(
    _: AdminAuth,
    session: DbSession,
) -> list[CookAccount]:
    """List every cook account (active and disabled), newest first, for the admin Cooks page (FR-007)."""
    return cooks.list_cooks(session)


@router.post("/{username}/deactivate", response_model=CookView)
def deactivate_cook(
    username: str,
    admin: AdminAuth,
    session: DbSession,
) -> CookAccount:
    """Disable a cook account (200); 404 for an unknown username. The cook loses access on its next request."""
    return cooks.deactivate(session, username, actor=admin.username)


@router.post("/{username}/reactivate", response_model=CookView)
def reactivate_cook(
    username: str,
    admin: AdminAuth,
    session: DbSession,
) -> CookAccount:
    """Re-enable a disabled cook account (200); 404 for an unknown username."""
    return cooks.reactivate(session, username, actor=admin.username)


@router.post("/{username}/reset-password", response_model=CookView)
def reset_cook_password(
    username: str,
    body: ResetCookPasswordRequest,
    admin: AdminAuth,
    session: DbSession,
) -> CookAccount:
    """Set a new password for a cook (admin-only recovery, FR-010): 200 on success, 422 weak, 404 unknown.

    Rotates the credential only — existing cook sessions stay valid until they expire (revocation is
    deactivation-driven, gating-model.md).
    """
    return cooks.reset_password(session, username, body.password, actor=admin.username)
