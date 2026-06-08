"""baseline: enable the pgvector extension

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-08

Enables the `vector` extension and nothing else, so later phases add pgvector columns on a
clean, migration-tracked baseline (data-model.md). No application tables exist yet.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the pgvector `vector` extension if it is not already present."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    """Drop the `vector` extension (reverses the baseline)."""
    op.execute("DROP EXTENSION IF EXISTS vector;")
