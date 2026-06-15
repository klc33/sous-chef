"""operator_accounts: named, admin-managed operator logins (008)

Revision ID: 0004_operator_accounts
Revises: 0003_embeddings
Create Date: 2026-06-15

Adds the single schema change for 008-admin-managed-operator-accounts: the `operator_accounts` table
holding named operator logins (bcrypt hash + admin/user role), replacing the single hardcoded operator.
The UNIQUE constraint on `username` is the race-safe backstop for the service's duplicate check (FR-009),
and the CHECK pins `role` to the two valid values. This is the operator identity domain only — no
cook-side table (`profiles`/`favorites`/`seen_history`/`recipes`) is referenced or altered (FR-019).
The first admin is NOT seeded here (it needs Vault); `bootstrap_admin()` runs at app startup so a
migrated-but-empty DB self-seeds on first boot. Written by hand so the constraints are explicit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "0004_operator_accounts"
down_revision = "0003_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the operator_accounts table with the unique username + role CHECK constraints."""
    op.create_table(
        "operator_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_operator_accounts_role"),
    )
    # UNIQUE index on username is both the DB-level uniqueness backstop (FR-009) and the login lookup path.
    op.create_index(
        "ix_operator_accounts_username", "operator_accounts", ["username"], unique=True
    )


def downgrade() -> None:
    """Drop the index then the table (reverse of upgrade)."""
    op.drop_index("ix_operator_accounts_username", table_name="operator_accounts")
    op.drop_table("operator_accounts")
