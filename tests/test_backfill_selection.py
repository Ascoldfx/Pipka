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
        user = User(
            id=1,
            name="Test",
            profile=UserProfile(
                target_titles=["Director Supply Chain"],
                preferred_countries=["de"],
            ),
        )
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
    monkeypatch.setattr(settings, "backfill_max_age_days", 31)
    monkeypatch.setattr(settings, "backfill_min_previous_score", 60)
    monkeypatch.setattr(settings, "backfill_ai_jobs_per_run", 30)
    monkeypatch.setattr(settings, "semantic_skip_enabled", False)

    await scheduler_service._backfill_score()

    assert selected == [high_germany.id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_gives_each_user_15_fresh_jobs_from_their_own_market(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now()
    async with factory() as session:
        users = [
            User(
                id=1,
                name="Germany",
                profile=UserProfile(
                    target_titles=["Director Supply Chain"],
                    preferred_countries=["de"],
                ),
            ),
            User(
                id=2,
                name="Singapore",
                profile=UserProfile(
                    target_titles=["Director Supply Chain"],
                    preferred_countries=["sg"],
                ),
            ),
        ]
        jobs = []
        for country in ("de", "sg"):
            for index in range(20):
                jobs.append(Job(
                    external_id=f"{country}-{index}",
                    source="test",
                    title="Director Supply Chain",
                    description="Supply chain leadership role.",
                    country=country,
                    posted_at=now - timedelta(hours=index),
                    url_status="active",
                    dedup_hash=f"{country}-{index}",
                ))
        session.add_all([*users, *jobs])
        await session.commit()

    selected: dict[int, list[str]] = {1: [], 2: []}
    call_order: list[int] = []

    async def score_selected(jobs, user, _session):
        call_order.append(user.id)
        selected[user.id].extend(job.external_id for job in jobs)
        return []

    async def no_hidden(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(scheduler_service, "async_session", factory)
    monkeypatch.setattr(scheduler_service, "_backfill_score_fn", lambda: score_selected)
    monkeypatch.setattr(scheduler_service, "get_hidden_job_ids", no_hidden)
    monkeypatch.setattr(scheduler_service, "get_hidden_dedup_hashes", no_hidden)
    monkeypatch.setattr(scheduler_service, "_backfill_round_robin_offset", 0)
    monkeypatch.setattr(settings, "backfill_max_age_days", 31)
    monkeypatch.setattr(settings, "backfill_ai_jobs_per_run", 30)
    monkeypatch.setattr(settings, "max_jobs_per_scoring_batch", 15)
    monkeypatch.setattr(settings, "semantic_skip_enabled", False)

    await scheduler_service._backfill_score()

    assert selected[1] == [f"de-{index}" for index in range(15)]
    assert selected[2] == [f"sg-{index}" for index in range(15)]
    assert call_order == [1, 2]

    selected = {1: [], 2: []}
    call_order.clear()
    await scheduler_service._backfill_score()

    assert call_order == [2, 1]
    await engine.dispose()


async def _nvidia_budget_fixture(monkeypatch, *, tier1: int, tier2: int):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now()
    async with factory() as session:
        user = User(
            id=1,
            name="Germany",
            profile=UserProfile(
                target_titles=["Director Supply Chain"],
                preferred_countries=["de"],
            ),
        )
        jobs = []
        for index in range(tier1):
            jobs.append(Job(
                external_id=f"t1-{index}", source="test", title="Director Supply Chain",
                description="Supply chain leadership role.", country="de",
                posted_at=now - timedelta(hours=index), url_status="active", dedup_hash=f"t1-{index}",
            ))
        for index in range(tier2):
            jobs.append(Job(
                external_id=f"t2-{index}", source="test", title="Supply Chain Manager",
                description="Supply chain management role.", country="de",
                posted_at=now - timedelta(hours=index), url_status="active", dedup_hash=f"t2-{index}",
            ))
        session.add_all([user, *jobs])
        await session.commit()

    selected: list[str] = []

    async def score_jobs_nvidia(jobs, _user, _session):
        selected.extend(job.external_id for job in jobs)
        return []

    async def no_hidden(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(scheduler_service, "async_session", factory)
    monkeypatch.setattr(scheduler_service, "_backfill_score_fn", lambda: score_jobs_nvidia)
    monkeypatch.setattr(scheduler_service, "get_hidden_job_ids", no_hidden)
    monkeypatch.setattr(scheduler_service, "get_hidden_dedup_hashes", no_hidden)
    monkeypatch.setattr(scheduler_service, "_backfill_round_robin_offset", 0)
    monkeypatch.setattr(settings, "backfill_max_age_days", 31)
    monkeypatch.setattr(settings, "backfill_ai_jobs_per_run", 30)
    monkeypatch.setattr(settings, "max_jobs_per_scoring_batch", 15)
    monkeypatch.setattr(settings, "nvidia_backfill_jobs_per_run", 40)
    monkeypatch.setattr(settings, "nvidia_backfill_max_seconds", 2700)
    monkeypatch.setattr(settings, "semantic_skip_enabled", False)
    return engine, selected


@pytest.mark.asyncio
async def test_nvidia_backfill_uses_its_own_larger_budget(monkeypatch):
    engine, selected = await _nvidia_budget_fixture(monkeypatch, tier1=50, tier2=0)

    await scheduler_service._backfill_score()

    assert selected == [f"t1-{index}" for index in range(40)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_nvidia_backfill_spends_leftover_budget_on_tier2(monkeypatch):
    engine, selected = await _nvidia_budget_fixture(monkeypatch, tier1=5, tier2=10)

    await scheduler_service._backfill_score()

    assert selected[:5] == [f"t1-{index}" for index in range(5)]
    assert sorted(selected[5:]) == sorted(f"t2-{index}" for index in range(10))
    await engine.dispose()


@pytest.mark.asyncio
async def test_nvidia_backfill_stops_at_time_budget(monkeypatch):
    engine, selected = await _nvidia_budget_fixture(monkeypatch, tier1=10, tier2=0)
    monkeypatch.setattr(settings, "nvidia_backfill_max_seconds", 0)

    await scheduler_service._backfill_score()

    assert selected == []
    await engine.dispose()
