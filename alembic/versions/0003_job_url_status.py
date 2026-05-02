"""url-liveness — add url_status / url_checked_at / url_check_failures to jobs

Revision ID: 0003_job_url_status
Revises: 0002_phase2_profile_hash
Create Date: 2026-04-27

Lays the groundwork for a daily HEAD-ping scheduler job that flags closed
postings and lets the dashboard hide them from inbox by default.

* ``url_status``           — 'active' | 'closed' | 'unreachable' | NULL
                             NULL = never checked yet, treated as active by readers.
* ``url_checked_at``       — last HEAD timestamp; ``NULL`` jobs go to the front of
                             the picker queue.
* ``url_check_failures``   — consecutive transient errors (5xx / network);
                             at 3 consecutive we flip to ``unreachable``.

Composite index ``ix_jobs_url_status_checked (url_status, url_checked_at)``
serves both the picker (oldest-first) and the dashboard "hide closed" filter.
"""
from __future__ import annotations

from alembic import op

revision = "0003_job_url_status"
down_revision = "0002_phase2_profile_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS url_status VARCHAR(20)")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS url_checked_at TIMESTAMP")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS url_check_failures INTEGER NOT NULL DEFAULT 0")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_url_status_checked
        ON jobs (url_status, url_checked_at)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_url_status_checked", table_name="jobs")
    op.drop_column("jobs", "url_check_failures")
    op.drop_column("jobs", "url_checked_at")
    op.drop_column("jobs", "url_status")
