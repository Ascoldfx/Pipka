"""add user credits and payment_transactions table

Revision ID: 0011_user_billing_and_transactions
Revises: 0010_nemotron_embeddings
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_user_billing"
down_revision = "0010_nemotron_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add credits and total_credits_purchased columns to users table
    op.add_column("users", sa.Column("credits", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("users", sa.Column("total_credits_purchased", sa.Integer(), nullable=False, server_default="0"))

    # 2. Create payment_transactions table
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("credits_added", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="cryptomus"),
        sa.Column("provider_tx_id", sa.String(length=255), nullable=True),
        sa.Column("payment_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_payment_transactions_user_id", "payment_transactions", ["user_id"])
    op.create_index("ix_payment_transactions_status", "payment_transactions", ["status"])
    op.create_index("ix_payment_transactions_provider_tx_id", "payment_transactions", ["provider_tx_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_provider_tx_id", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_status", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_user_id", table_name="payment_transactions")
    op.drop_table("payment_transactions")
    op.drop_column("users", "total_credits_purchased")
    op.drop_column("users", "credits")
