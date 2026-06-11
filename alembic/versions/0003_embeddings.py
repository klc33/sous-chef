"""embeddings: add recipes.embedding vector(1536) + HNSW cosine index

Revision ID: 0003_embeddings
Revises: 0002_catalog
Create Date: 2026-06-10

Adds the single schema change for 003-intelligent-behavior: a pgvector embedding column on `recipes`
plus an HNSW index for cosine-distance ANN search. Additive and nullable, so there is no backfill —
existing rows get their vector on the next `make ingest` (the offline embed stage). The dimension
(1536) is pinned here and asserted against `settings.embeddings_dim` at startup; changing the embedding
model's dimension requires a new migration, never an in-place edit. pgvector itself is already enabled
by 0001_baseline. Written by hand so the index type/opclass are explicit and reviewable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "0003_embeddings"
down_revision = "0002_catalog"
branch_labels = None
depends_on = None

# Must match app.config.MIGRATION_EMBEDDINGS_DIM and the model's Vector(...) width.
_EMBEDDING_DIM = 1536


def upgrade() -> None:
    """Add the nullable embedding column, then build the HNSW cosine index over it."""
    # Nullable + additive → no backfill; null-embedding rows are simply excluded by vector search.
    op.add_column("recipes", sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True))
    # HNSW with the cosine opclass so the `<=>` order-by in search_by_vector uses the index. At this
    # corpus size the planner may still pick an exact scan, which is fine — the index future-proofs growth.
    op.create_index(
        "ix_recipes_embedding_hnsw",
        "recipes",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop the index first, then the column (reverse of upgrade)."""
    op.drop_index("ix_recipes_embedding_hnsw", table_name="recipes")
    op.drop_column("recipes", "embedding")
