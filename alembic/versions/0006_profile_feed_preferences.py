"""add hidden countries and remove the obsolete salary preference

Revision ID: 0006_profile_feed_preferences
Revises: 0005_cascade_fks
Create Date: 2026-07-26

``hidden_countries`` is a presentation preference: jobs keep being collected
and scored, but the selected countries are excluded from the default feed.
An explicit country filter overrides it.

``min_salary`` is removed because salary is absent from most source listings
and is no longer used by either the rule-based or AI scoring paths.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_profile_feed_preferences"
down_revision = "0005_cascade_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("user_profiles")}
    add_hidden = "hidden_countries" not in columns
    drop_salary = "min_salary" in columns
    if not add_hidden and not drop_salary:
        # The baseline creates from current metadata on a fresh database.
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_profiles") as batch_op:
            if add_hidden:
                batch_op.add_column(sa.Column("hidden_countries", sa.JSON(), nullable=True))
            if drop_salary:
                batch_op.drop_column("min_salary")
        return

    if add_hidden:
        op.add_column("user_profiles", sa.Column("hidden_countries", sa.JSON(), nullable=True))
    if drop_salary:
        op.drop_column("user_profiles", "min_salary")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("user_profiles")}
    add_salary = "min_salary" not in columns
    drop_hidden = "hidden_countries" in columns
    if not add_salary and not drop_hidden:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_profiles") as batch_op:
            if add_salary:
                batch_op.add_column(sa.Column("min_salary", sa.Integer(), nullable=True))
            if drop_hidden:
                batch_op.drop_column("hidden_countries")
        return

    if add_salary:
        op.add_column("user_profiles", sa.Column("min_salary", sa.Integer(), nullable=True))
    if drop_hidden:
        op.drop_column("user_profiles", "hidden_countries")
