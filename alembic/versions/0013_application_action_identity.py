"""make each user/vacancy action unique

Revision ID: 0013_application_identity
Revises: 0012_onboarding_and_feedback
Create Date: 2026-08-19
"""
from __future__ import annotations

from alembic import op

revision = "0013_application_identity"
down_revision = "0012_onboarding_and_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the newest action if historical races produced duplicate rows.
    # Move all history to that survivor before deleting the older duplicates.
    op.execute(
        """
        CREATE TEMPORARY TABLE application_dedup_map ON COMMIT DROP AS
        SELECT
            id AS old_id,
            first_value(id) OVER (
                PARTITION BY user_id, job_id
                ORDER BY updated_at DESC NULLS LAST, id DESC
            ) AS survivor_id
        FROM applications
        """
    )
    op.execute(
        """
        UPDATE application_history AS history
        SET application_id = mapping.survivor_id
        FROM application_dedup_map AS mapping
        WHERE history.application_id = mapping.old_id
          AND mapping.old_id <> mapping.survivor_id
        """
    )
    op.execute(
        """
        DELETE FROM applications AS application
        USING application_dedup_map AS mapping
        WHERE application.id = mapping.old_id
          AND mapping.old_id <> mapping.survivor_id
        """
    )
    op.drop_index("ix_applications_user_job", table_name="applications")
    op.create_index(
        "ix_applications_user_job",
        "applications",
        ["user_id", "job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_applications_user_job", table_name="applications")
    op.create_index(
        "ix_applications_user_job",
        "applications",
        ["user_id", "job_id"],
        unique=False,
    )
