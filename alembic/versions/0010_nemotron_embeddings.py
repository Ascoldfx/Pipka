"""switch semantic index to 2048-dimensional NVIDIA Nemotron embeddings

Revision ID: 0010_nemotron_embeddings
Revises: 0009_geographic_dedup_hash
Create Date: 2026-08-06

Existing Gemini vectors cannot be compared with Nemotron vectors. Clear both
collections, change the pgvector dimension, then rebuild them incrementally.
"""
from __future__ import annotations

from alembic import op

revision = "0010_nemotron_embeddings"
down_revision = "0009_geographic_dedup_hash"
branch_labels = None
depends_on = None


def _create_indexes() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw "
        "ON jobs USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_profiles_embedding_hnsw "
        "ON user_profiles USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_user_profiles_embedding_hnsw")
    op.execute("UPDATE jobs SET embedding = NULL, embedding_model = NULL, embedding_updated_at = NULL")
    op.execute(
        "UPDATE user_profiles SET embedding = NULL, embedding_model = NULL, "
        "embedding_updated_at = NULL, embedding_profile_hash = NULL"
    )
    op.execute("ALTER TABLE jobs ALTER COLUMN embedding TYPE vector(2048) USING embedding::vector(2048)")
    op.execute(
        "ALTER TABLE user_profiles ALTER COLUMN embedding TYPE vector(2048) USING embedding::vector(2048)"
    )
    # Note: We cannot create HNSW indexes for 2048-dimensional vectors because pgvector
    # has a hard limit of 2000 dimensions for HNSW indexes. Flat scan (exact search)
    # will be used instead, which is extremely fast for small datasets (under 10k rows).



def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_user_profiles_embedding_hnsw")
    op.execute("UPDATE jobs SET embedding = NULL, embedding_model = NULL, embedding_updated_at = NULL")
    op.execute(
        "UPDATE user_profiles SET embedding = NULL, embedding_model = NULL, "
        "embedding_updated_at = NULL, embedding_profile_hash = NULL"
    )
    op.execute("ALTER TABLE jobs ALTER COLUMN embedding TYPE vector(768) USING embedding::vector(768)")
    op.execute(
        "ALTER TABLE user_profiles ALTER COLUMN embedding TYPE vector(768) USING embedding::vector(768)"
    )
    _create_indexes()
