"""day-3 — add ON DELETE CASCADE to foreign keys that should cascade

Revision ID: 0005_cascade_fks
Revises: 0004_search_embeddings
Create Date: 2026-05-07

Audit finding #17 (medium severity): every FK in the schema is currently
``ON DELETE NO ACTION``. ``_cleanup_old_jobs`` deletes batches of Job
rows older than 45 days; without cascade we have to manually pre-delete
all referencing rows in ``job_scores`` and ``applications``, which is
exactly what the existing scheduler code does (DELETE in a specific
order). One missed FK = constraint violation, transaction rollback,
half-cleaned table.

Cascade semantics that we want:

* ``user_profiles.user_id        → users.id``    : CASCADE (delete user → drop profile)
* ``job_scores.user_id           → users.id``    : CASCADE
* ``job_scores.job_id            → jobs.id``     : CASCADE (cleanup loop simplifies)
* ``applications.user_id         → users.id``    : CASCADE
* ``applications.job_id          → jobs.id``     : CASCADE
* ``application_history.application_id → applications.id`` : CASCADE
* ``search_subscriptions.user_id → users.id``    : CASCADE

PostgreSQL has no "ALTER CONSTRAINT ... ON DELETE CASCADE" syntax — we
have to drop and re-add. Each constraint is independent, no transaction
ordering concern.

The matching ``ondelete="CASCADE"`` is being added to the SQLAlchemy
models in the same change so future ``Base.metadata.create_all`` paths
(fresh dev DB) inherit the cascade automatically.
"""
from __future__ import annotations

from alembic import op

revision = "0005_cascade_fks"
down_revision = "0004_search_embeddings"
branch_labels = None
depends_on = None


# (table, constraint_name, column, ref_table, ref_column)
_FKS: tuple[tuple[str, str, str, str, str], ...] = (
    ("user_profiles",        "user_profiles_user_id_fkey",            "user_id",        "users",        "id"),
    ("job_scores",           "job_scores_user_id_fkey",               "user_id",        "users",        "id"),
    ("job_scores",           "job_scores_job_id_fkey",                "job_id",         "jobs",         "id"),
    ("applications",         "applications_user_id_fkey",             "user_id",        "users",        "id"),
    ("applications",         "applications_job_id_fkey",              "job_id",         "jobs",         "id"),
    ("application_history",  "application_history_application_id_fkey","application_id", "applications", "id"),
    ("search_subscriptions", "search_subscriptions_user_id_fkey",     "user_id",        "users",        "id"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite would need table rebuild — dev databases aren't worth the
        # complexity here. Skip; dev path uses fresh create_all which picks
        # up ondelete="CASCADE" from the model.
        return
    for table, name, col, ref_table, ref_col in _FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col}) "
            f"ON DELETE CASCADE"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, name, col, ref_table, ref_col in _FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({col}) REFERENCES {ref_table}({ref_col})"
        )
