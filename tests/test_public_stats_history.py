from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import stats as stats_api
from app.models import Base
from app.models.job import Job, JobScore
from app.models.user import User


@pytest.mark.asyncio
async def test_public_stats_include_month_and_all_time_history(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        user = User(id=1, name="Test", is_active=True)
        old_job = Job(
            id=1,
            external_id="old-1",
            source="adzuna",
            title="Old role",
            dedup_hash="old-1",
            scraped_at=now - timedelta(days=45),
        )
        recent_top = Job(
            id=2,
            external_id="new-1",
            source="indeed",
            title="Recent top role",
            dedup_hash="new-1",
            scraped_at=now - timedelta(days=5),
        )
        recent_rejected = Job(
            id=3,
            external_id="new-2",
            source="gupy",
            title="Recent rejected role",
            dedup_hash="new-2",
            scraped_at=now - timedelta(days=2),
        )
        session.add_all([user, old_job, recent_top, recent_rejected])
        await session.flush()
        session.add_all(
            [
                JobScore(
                    job_id=old_job.id,
                    user_id=user.id,
                    score=80,
                    ai_analysis="Historical match",
                    scored_at=now - timedelta(days=45),
                ),
                JobScore(
                    job_id=recent_top.id,
                    user_id=user.id,
                    score=90,
                    ai_analysis="Recent match",
                    scored_at=now - timedelta(days=4),
                ),
                JobScore(
                    job_id=recent_rejected.id,
                    user_id=user.id,
                    score=0,
                    ai_analysis=None,
                    scored_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(stats_api, "async_session", factory)
    stats_api._PUBLIC_STATS_CACHE.clear()

    payload = await stats_api.get_public_stats()

    assert payload["history"]["month"] == {
        "days": 30,
        "jobs_collected": 2,
        "prefilter_rejected": 1,
        "ai_analyses_performed": 2,
        "top_matches": 1,
        "active_sources": 2,
    }
    assert payload["history"]["all_time"] == {
        "jobs_collected": 3,
        "prefilter_rejected": 1,
        "ai_analyses_performed": 3,
        "top_matches": 2,
        "active_sources": 3,
    }
    assert payload["total_jobs_collected"] == 3
    assert payload["top_matches"] == 2

    await engine.dispose()
