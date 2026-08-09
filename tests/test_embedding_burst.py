from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import embedding_service, scheduler_service


@pytest.mark.asyncio
async def test_burst_runs_only_when_pending_queue_is_strictly_above_threshold(monkeypatch):
    monkeypatch.setattr(embedding_service, "_enabled", lambda _session: True)
    pending = AsyncMock(return_value=100)
    index_jobs = AsyncMock(return_value=70)
    monkeypatch.setattr(embedding_service, "_count_indexable_jobs", pending)
    monkeypatch.setattr(embedding_service, "_index_jobs", index_jobs)

    skipped = await embedding_service.index_missing_embeddings(
        object(), min_pending_jobs=100, include_profiles=False
    )

    assert skipped == {"jobs": 0, "profiles": 0, "pending": 100, "skipped": 1}
    index_jobs.assert_not_awaited()

    pending.return_value = 101
    indexed = await embedding_service.index_missing_embeddings(
        object(), min_pending_jobs=100, include_profiles=False
    )

    assert indexed == {"jobs": 70, "profiles": 0, "pending": 101, "skipped": 0}
    index_jobs.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_burst_passes_threshold_and_skips_profiles(monkeypatch):
    session = object()

    @asynccontextmanager
    async def fake_session():
        yield session

    index_missing = AsyncMock(
        return_value={"jobs": 70, "profiles": 0, "pending": 648, "skipped": 0}
    )
    record = AsyncMock()
    monkeypatch.setattr(scheduler_service, "async_session", fake_session)
    monkeypatch.setattr(embedding_service, "index_missing_embeddings", index_missing)
    monkeypatch.setattr(scheduler_service, "record_ops_event", record)
    monkeypatch.setattr(settings, "embedding_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "nvidia")

    await scheduler_service._embed_index(min_pending_jobs=100)

    index_missing.assert_awaited_once_with(
        session, min_pending_jobs=100, include_profiles=False
    )
    assert record.await_args.args == ("embedding_index", "success")
    assert record.await_args.kwargs["source"] == "nvidia_embedding"
    assert record.await_args.kwargs["payload"]["burst_threshold"] == 100


@pytest.mark.asyncio
async def test_burst_does_not_run_for_non_nvidia_provider(monkeypatch):
    index_missing = AsyncMock()
    monkeypatch.setattr(embedding_service, "index_missing_embeddings", index_missing)
    monkeypatch.setattr(settings, "embedding_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "gemini")

    await scheduler_service._embed_index(min_pending_jobs=100)

    index_missing.assert_not_awaited()
