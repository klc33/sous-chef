"""ORM model for a cook-facing login identity (009-admin-managed-cook-accounts).

`CookAccount` is a named, admin-provisioned login that replaces the passwordless `X-Profile-ID`. Its
`id` — an opaque UUID4 **string** — is the owner key threaded through the app exactly where the
profile-ID header value used to be, so `profiles`/`favorites`/`seen_history` re-key to it with one FK and
no column-type migration (data-model.md). Each row carries a bcrypt `password_hash` (never plaintext,
never a Vault secret — a hash is non-reversible) and, unlike an operator account (008), **NO role** —
every cook is simply a cook (the managing authority is an operator/admin from 008). This is the cook
identity domain only; it is isolated from the operator domain (separate table, separate signing key, and
a `typ:"cook"` token claim), so the two credentials never cross.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_cook_id() -> str:
    """Generate the opaque UUID4 string used as a cook account's id (the owner key).

    Returned as a `str` (not a native UUID) so the Text owner-key columns on `profiles`/`favorites`/
    `seen_history` can reference it via FK without a type change — the deliberately minimal-churn re-key.
    """
    return str(uuid.uuid4())


class CookAccount(Base):
    """A named cook login: opaque string id (the owner key), unique username, bcrypt hash, active flag.

    The `id` is a Text UUID4 string (not a native `UUID` column) so the existing Text owner-key FKs align
    without a type migration (data-model.md). Deactivation is a flag (`is_active`), never a hard delete, so
    a disabled cook is retained. There is deliberately NO `role` column — cooks are role-less; the only
    role-bearing identity in the system is the operator account (008).
    """

    __tablename__ = "cook_accounts"

    # Opaque UUID4 string PK; this is the owner key the app threads where `profile_id` (the header) was.
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_new_cook_id)
    # Unique login handle, case-sensitive; uniqueness is a DB constraint (FR-006), not just app code.
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Optional human label; the service defaults it to `username` when blank.
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deactivation flag (FR-009): a disabled cook cannot sign in and loses access on its next request.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # bcrypt hash ONLY — never plaintext, never recoverable (FR-016); not a Vault secret (it is a hash).
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Username of the operator/admin (008) who created this cook; NULL for the seeded demo cook (audit, FR-016).
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped on status/password change so the audit timeline reflects the last mutation.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
