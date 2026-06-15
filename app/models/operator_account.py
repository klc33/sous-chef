"""ORM model for an operator-console login identity (008-admin-managed-operator-accounts).

`OperatorAccount` is a named, admin-managed login that replaces the single hardcoded operator
(`OPERATOR_USERNAME` + Vault `OPERATOR_PASSWORD_HASH`). Each row carries a bcrypt `password_hash`
(never plaintext, never a Vault secret — a hash is non-reversible) and a `role` the backend trusts
to enforce the admin-vs-user split. This is the operator identity domain only — it has NO relation to
the cook side (`profiles`/`favorites`/`seen_history`), which stays passwordless. See
specs/008-admin-managed-operator-accounts/data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OperatorAccount(Base):
    """A named operator login: unique username, bcrypt password hash, and an admin/user role.

    Deactivation is a flag (`is_active`), never a hard delete, so a disabled account is retained for
    audit (data-model state transitions). The `CHECK (role IN ('admin','user'))` constraint mirrors the
    `Diet` enum pattern on `profiles` so an out-of-range role fails at the DB, not just in app code.
    """

    __tablename__ = "operator_accounts"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_operator_accounts_role"),
    )

    # Surrogate key (matches the seen_history UUID style); the username is the human/login handle.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique login handle, case-sensitive; uniqueness is a DB constraint (FR-009), not just app code.
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Optional human label; the service defaults it to `username` when blank.
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Permission level (FR-005); the CHECK constraint keeps it to exactly 'admin' or 'user'.
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # Deactivation flag (FR-008): a disabled account cannot sign in and loses access on its next request.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # bcrypt hash ONLY — never plaintext, never recoverable (FR-016); not a Vault secret (it is a hash).
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Username of the admin who created this row; NULL for the bootstrap admin (audit trail, FR-018).
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped on role/status/password change so the audit timeline reflects the last mutation.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
