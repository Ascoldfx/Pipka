from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.services import scheduler_service


@pytest.mark.asyncio
async def test_backfill_rescores_only_recent_high_scoring_german_jobs(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now()
    async with factory() as session:
        user = User(id=1, name="Test", profile=UserProfile(target_titles=["Director Supply Chain"]))
        high_germany = Job(
            external_id="high-de",
            source="test",
            title="Director Supply Chain",
            description="Supply chain leadership role.",
            country="de",
            posted_at=now - timedelta(days=3),
            url_status="active",
            dedup_hash="high-de",
        )
        low_germany = Job(
            external_id="low-de",
            source="test",
            title="Director Supply Chain",
            description="Supply chain leadership role.",
            country="de",
            posted_at=now - timedelta(days=3),
            url_status="active",
            dedup_hash="low-de",
        )
        high_other_country = Job(
            external_id="high-ae",
            source="test",
            title="Director Supply Chain",
            description="Supply chain leadership role.",
            country="ae",
            posted_at=now - timedelta(days=3),
            url_status="active",
            dedup_hash="high-ae",
        )
        high_old = Job(
            external_id="high-old",
            source="test",
            title="Director Supply Chain",
            description="Supply chain leadership role.",
            country="de",
            posted_at=now - timedelta(days=32),
            url_status="active",
            dedup_hash="high-old",
        )
        session.add_all([user, high_germany, low_germany, high_other_country, high_old])
        await session.flush()
        session.add_all([
            JobScore(job_id=high_germany.id, user_id=user.id, score=60, profile_hash="old", model_version="gemini:old"),
            JobScore(job_id=low_germany.id, user_id=user.id, score=59, profile_hash="old", model_version="gemini:old"),
            JobScore(job_id=high_other_country.id, user_id=user.id, score=90, profile_hash="old", model_version="gemini:old"),
            JobScore(job_id=high_old.id, user_id=user.id, score=90, profile_hash="old", model_version="gemini:old"),
        ])
        await session.commit()

    selected: list[int] = []

    async def score_selected(jobs, _user, _session):
        selected.extend(job.id for job in jobs)
        return []

    async def no_hidden(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(scheduler_service, "async_session", factory)
    monkeypatch.setattr(scheduler_service, "_backfill_score_fn", lambda: score_selected)
    monkeypatch.setattr(scheduler_service, "get_hidden_job_ids", no_hidden)
    monkeypatch.setattr(scheduler_service, "get_hidden_dedup_hashes", no_hidden)
    monkeypatch.setattr(settings, "backfill_country", "de")
    monkeypatch.setattr(settings, "backfill_max_age_days", 31)
    monkeypatch.setattr(settings, "backfill_min_previous_score", 60)
    monkeypatch.setattr(settings, "backfill_ai_jobs_per_run", 30)
    monkeypatch.setattr(settings, "semantic_skip_enabled", False)

    await scheduler_service._backfill_score()

    assert selected == [high_germany.id]
    await engine.dispose()
