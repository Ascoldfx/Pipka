"""search-indexing — add full-text search and pgvector embeddings

Revision ID: 0004_search_embeddings
Revises: 0003_job_url_status
Create Date: 2026-05-01

Adds:
* jobs.search_vector — generated tsvector over title/company/description
* GIN index for fast text search
* pgvector extension + embedding columns for jobs and user_profiles
* HNSW cosine indexes for semantic nearest-neighbour retrieval

PostgreSQL-only. SQLite dev databases no-op here.
"""
from __future__ import annotations

from alembic import op

revision = "0004_search_embeddings"
down_revision = "0003_job_url_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        ALTER TABLE jobs
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(company_name, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(description, '')), 'B')
        ) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_search_vector
        ON jobs USING gin (search_vector)
        """
    )
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS embedding vector(768)")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(120)")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMP")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw
            ON jobs USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL;
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'Skipping ix_jobs_embedding_hnsw: %', SQLERRM;
        END $$;
        """
    )
    op.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS embedding vector(768)")
    op.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(120)")
    op.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMP")
    op.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS embedding_profile_hash VARCHAR(64)")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS ix_user_profiles_embedding_hnsw
            ON user_profiles USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL;
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'Skipping ix_user_profiles_embedding_hnsw: %', SQLERRM;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_user_profiles_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_jobs_search_vector")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS embedding_profile_hash")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS embedding_updated_at")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS embedding_updated_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS search_vector")
