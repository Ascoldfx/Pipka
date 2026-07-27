"""separate excluded companies from content keywords

Revision ID: 0007_excluded_companies
Revises: 0006_profile_feed_preferences
Create Date: 2026-07-27

Historically, automatically blocked company names were appended to
``excluded_keywords``.  The pre-filter searched those values across the full
job description, so blocking the company "SAP" also rejected unrelated jobs
that merely listed SAP as a required tool.  This migration adds a dedicated
company list and moves values that exactly match an existing company name.
Invalid upstream placeholders such as ``nan`` are discarded.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0007_excluded_companies"
down_revision = "0006_profile_feed_preferences"
branch_labels = None
depends_on = None

_INVALID_VALUES = {"", "nan", "none", "null", "n/a", "unknown"}


def _normalise(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _add_column_if_needed(bind) -> None:
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("user_profiles")
    }
    if "excluded_companies" in columns:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_profiles") as batch_op:
            batch_op.add_column(
                sa.Column("excluded_companies", sa.JSON(), nullable=True)
            )
        return
    op.add_column(
        "user_profiles",
        sa.Column("excluded_companies", sa.JSON(), nullable=True),
    )


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_needed(bind)

    company_names = {
        _normalise(row[0])
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT company_name FROM jobs "
                "WHERE company_name IS NOT NULL"
            )
        )
        if _normalise(row[0]) not in _INVALID_VALUES
    }
    profiles = bind.execute(
        sa.text(
            "SELECT id, excluded_keywords, excluded_companies "
            "FROM user_profiles"
        )
    ).all()

    profile_table = sa.table(
        "user_profiles",
        sa.column("id", sa.Integer()),
        sa.column("excluded_keywords", sa.JSON()),
        sa.column("excluded_companies", sa.JSON()),
    )
    for profile_id, raw_keywords, raw_companies in profiles:
        keywords: list[str] = []
        companies = [
            value
            for value in _as_list(raw_companies)
            if _normalise(value) not in _INVALID_VALUES
        ]
        seen_companies = {_normalise(value) for value in companies}

        for value in _as_list(raw_keywords):
            normalised = _normalise(value)
            if normalised in _INVALID_VALUES:
                continue
            if normalised in company_names:
                if normalised not in seen_companies:
                    companies.append(value)
                    seen_companies.add(normalised)
            else:
                keywords.append(value)

        bind.execute(
            sa.update(profile_table)
            .where(profile_table.c.id == profile_id)
            .values(
                excluded_keywords=keywords,
                excluded_companies=companies,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("user_profiles")
    }
    if "excluded_companies" not in columns:
        return

    profiles = bind.execute(
        sa.text(
            "SELECT id, excluded_keywords, excluded_companies "
            "FROM user_profiles"
        )
    ).all()
    profile_table = sa.table(
        "user_profiles",
        sa.column("id", sa.Integer()),
        sa.column("excluded_keywords", sa.JSON()),
    )
    for profile_id, raw_keywords, raw_companies in profiles:
        merged: list[str] = []
        seen: set[str] = set()
        for value in _as_list(raw_keywords) + _as_list(raw_companies):
            normalised = _normalise(value)
            if normalised in _INVALID_VALUES or normalised in seen:
                continue
            merged.append(value)
            seen.add(normalised)
        bind.execute(
            sa.update(profile_table)
            .where(profile_table.c.id == profile_id)
            .values(excluded_keywords=merged)
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_profiles") as batch_op:
            batch_op.drop_column("excluded_companies")
        return
    op.drop_column("user_profiles", "excluded_companies")
