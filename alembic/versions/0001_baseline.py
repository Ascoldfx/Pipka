"""baseline — establish Alembic version tracking against the existing schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-26

This migration is intentionally idempotent: it calls ``Base.metadata.create_all``
on the bound connection. On production (where every table already exists) it is
a no-op. On a fresh dev/test database it materialises the full schema.

Future schema changes go in their own migration files. Once this baseline runs
successfully on prod (recorded in ``alembic_version``), Alembic owns the schema.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Importing inside the function keeps revision discovery cheap and avoids
    # tight coupling between Alembic startup and the model layer at import time.
    from app.models import Base

    bind = op.get_bind()
    # Underlying conn is sync inside Alembic — run_sync isn't required.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # Refusing destructive downgrades on the baseline — nuking every table
    # would silently delete production data on a misclick. Operators who need
    # to nuke and rebuild can drop manually.
    raise RuntimeError(
        "Refusing to downgrade past the baseline migration. "
        "Drop tables manually if you really want a clean slate."
    )
