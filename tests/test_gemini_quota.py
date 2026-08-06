from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.job import Job
from app.models.ops_event import OpsEvent
from app.models.user import UserProfile
from app.scoring import gemini_matcher
from app.scoring.matcher import analyze_single_job


@pytest.mark.asyncio
async def test_daily_gemini_budget_is_persistent_across_calls(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(gemini_matcher, "async_session", factory)
    monkeypatch.setattr(settings, "gemini_daily_request_limit", 2)
    monkeypatch.setattr(settings, "gemini_scoring_model", "gemini-3.6-flash")

    assert await gemini_matcher._claim_daily_request_slot() is True
    assert await gemini_matcher._claim_daily_request_slot() is True
    assert await gemini_matcher._claim_daily_request_slot() is False

    async with factory() as session:
        attempts = (
            await session.execute(select(OpsEvent).where(OpsEvent.event_type == "gemini_request"))
        ).scalars().all()
    assert len(attempts) == 2
    assert {event.status for event in attempts} == {"attempt"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_quota_error_is_not_retried_and_opens_breaker(monkeypatch):
    class ResourceExhausted(Exception):
        status_code = 429

    calls = 0

    async def exhausted(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ResourceExhausted("daily quota")

    record = AsyncMock()
    exhaust = AsyncMock()
    monkeypatch.setattr(gemini_matcher, "generate_gemini_content", exhausted)
    monkeypatch.setattr(gemini_matcher, "_claim_daily_request_slot", AsyncMock(return_value=True))
    monkeypatch.setattr(gemini_matcher, "record_ops_event", record)
    monkeypatch.setattr(gemini_matcher, "_record_exhaust", exhaust)

    result = await gemini_matcher._call_gemini_raw([], "profile")

    assert result == []
    assert calls == 1
    record.assert_awaited_once()
    exhaust.assert_awaited_once_with("ResourceExhausted", immediate=True)


@pytest.mark.asyncio
async def test_manual_analysis_does_not_consume_gemini_batch_budget(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_detailed_analysis_enabled", False)

    result = await analyze_single_job(Job(title="Director Supply Chain"), UserProfile())

    assert "зарезервирован" in result
