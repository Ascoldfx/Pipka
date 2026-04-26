"""phase 2 — add profile_hash + model_version to job_scores

Revision ID: 0002_phase2_profile_hash
Revises: 0001_baseline
Create Date: 2026-04-26

Adds two nullable columns to ``job_scores`` and a composite index for fast
cache lookups of the form
``WHERE user_id=? AND profile_hash=? AND model_version=?``.

Existing rows keep ``profile_hash=NULL`` and ``model_version=NULL`` —
treated as "legacy / unknown provenance" by the application layer. They
are not invalidated automatically; new scoring calls will populate the
new columns going forward.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_profile_hash"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_scores", sa.Column("profile_hash", sa.String(64), nullable=True))
    op.add_column("job_scores", sa.Column("model_version", sa.String(64), nullable=True))
    op.create_index(
        "ix_job_scores_user_profile_model",
        "job_scores",
        ["user_id", "profile_hash", "model_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_scores_user_profile_model", table_name="job_scores")
    op.drop_column("job_scores", "model_version")
    op.drop_column("job_scores", "profile_hash")
