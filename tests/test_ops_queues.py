from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring.profile_hash import compute_profile_hash
from app.services.ops_service import build_ops_overview


@pytest.mark.asyncio
async def test_ops_reports_actionable_queues_separately_from_archive():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # Embeddings are added by Alembic in PostgreSQL; SQLite tests need the
        # lightweight column because the scoped queue intentionally uses raw
        # pgvector-compatible SQL.
        await connection.execute(text("ALTER TABLE jobs ADD COLUMN embedding TEXT"))

    now = datetime.now()
    async with factory() as session:
        profile = UserProfile(target_titles=["Director Supply Chain"])
        user = User(id=1, name="Owner", role="admin", profile=profile)
        fresh_unscored_de = Job(
            external_id="fresh-de",
            source="test",
            title="Head of Procurement",
            country="de",
            posted_at=now - timedelta(days=1),
            dedup_hash="fresh-de",
        )
        fresh_scored_de = Job(
            external_id="scored-de",
            source="test",
            title="Director Supply Chain",
            country="de",
            posted_at=now - timedelta(days=1),
            dedup_hash="scored-de",
        )
        fresh_unscored_sg = Job(
            external_id="fresh-sg",
            source="test",
            title="Head of Procurement",
            country="sg",
            posted_at=now - timedelta(days=1),
            dedup_hash="fresh-sg",
        )
        old_unscored_de = Job(
            external_id="old-de",
            source="test",
            title="Head of Procurement",
            country="de",
            posted_at=now - timedelta(days=40),
            dedup_hash="old-de",
        )
        session.add_all([user, fresh_unscored_de, fresh_scored_de, fresh_unscored_sg, old_unscored_de])
        await session.flush()
        session.add(
            JobScore(
                user_id=user.id,
                job_id=fresh_scored_de.id,
                score=85,
                profile_hash=compute_profile_hash(profile),
                model_version="prefilter",
            )
        )
        await session.commit()

        overview = await build_ops_overview(
            session,
            user_id=user.id,
            window_hours=24,
            next_run_at=None,
            scan_running=False,
        )

    assert overview["queues"]["scoring"]["pending"] == 1
    assert overview["queues"]["embeddings"]["pending"] == 1
    assert overview["queues"]["archive"]["unscored_total"] == 3
    await engine.dispose()
