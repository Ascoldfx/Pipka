"""preserve same-company vacancies in different geographies

Revision ID: 0009_geographic_dedup_hash
Revises: 0008_requeue_semantic_skips
Create Date: 2026-08-01

The original hash used only title and company, so a Singapore posting could
hide a distinct Dubai or Saudi posting with the same role name. Recompute all
stored hashes with the v2 title/company/country/location identity used by the
collector.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import sqlalchemy as sa

from alembic import op

revision = "0009_geographic_dedup_hash"
down_revision = "0008_requeue_semantic_skips"
branch_labels = None
depends_on = None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    for suffix in (
        "gmbh", " ag", " ltd", " inc", " se", " co.", "& co", " mbh",
        " kg", " e.v.", " ohg", " ug", " sarl", " bv", " nv",
    ):
        text = text.replace(suffix, "")
    text = re.sub(
        r"\s*-\s*(system engineering|admin|ingenieur|it|engineering).*$",
        "",
        text,
    )
    text = re.sub(r"\s*\([mwfd/]+\)\s*", " ", text)
    text = re.sub(r"\s*\(all genders?\)\s*", " ", text)
    text = re.sub(r"\s*\(m/f/x\)\s*", " ", text)
    text = text.replace("senior ", "")
    text = re.sub(
        r"\s*[|–—-]\s*(berlin|münchen|munich|hamburg|frankfurt|"
        r"düsseldorf|köln|cologne|stuttgart|leipzig|dresden|hannover|"
        r"nürnberg|dortmund|essen|bremen|bonn)\b.*$",
        "",
        text,
    )
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\d{5}", "", text)
    return text.strip()


def _v2_hash(row: dict) -> str:
    components = (
        _normalize(row["title"] or ""),
        _normalize(row["company_name"] or ""),
        _normalize(row["country"] or ""),
        _normalize(row["location"] or ""),
    )
    return hashlib.sha256(("v2|" + "|".join(components)).encode()).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, title, company_name, country, location FROM jobs"
        )
    ).mappings().all()
    if not rows:
        return
    bind.execute(
        sa.text("UPDATE jobs SET dedup_hash = :dedup_hash WHERE id = :id"),
        [
            {"id": row["id"], "dedup_hash": _v2_hash(dict(row))}
            for row in rows
        ],
    )


def downgrade() -> None:
    # A v1 hash cannot represent two geographically distinct vacancies and
    # may violate the unique index after v2 data has been collected.
    return None
