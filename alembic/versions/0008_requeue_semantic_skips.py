"""requeue vacancies previously rejected by semantic similarity

Revision ID: 0008_requeue_semantic_skips
Revises: 0007_excluded_companies
Create Date: 2026-08-01

Embedding similarity is an approximate ranking signal, not an authoritative
rejection rule. Remove synthetic semantic-skip scores so every affected job is
re-evaluated by the current deterministic pre-filter and AI scorer.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_requeue_semantic_skips"
down_revision = "0007_excluded_companies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM job_scores WHERE model_version = 'semantic_skip'"))


def downgrade() -> None:
    # Deleted synthetic scores cannot be reconstructed, and restoring them
    # would reintroduce the false-negative behaviour this migration removes.
    return None
