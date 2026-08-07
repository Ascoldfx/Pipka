"""add onboarded column and user_feedbacks table

Revision ID: 0012_onboarding_and_feedback
Revises: 0011_user_billing
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_onboarding_and_feedback"
down_revision = "0011_user_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add onboarded column to users table
    op.add_column("users", sa.Column("onboarded", sa.Boolean(), nullable=False, server_default="false"))

    # 2. Create user_feedbacks table
    op.create_table(
        "user_feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="general"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_user_feedbacks_user_id", "user_feedbacks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_feedbacks_user_id", table_name="user_feedbacks")
    op.drop_table("user_feedbacks")
    op.drop_column("users", "onboarded")
