from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.scoring import gemini_matcher
from app.scoring.nvidia_matcher import score_jobs_nvidia
from app.services.scheduler_service import (
    _backfill_score_fn,
    _nvidia_idle_rescore,
    _score_backfill_batch,
)


def test_backfill_prefers_gemini_when_both_providers_are_available(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")
    monkeypatch.setattr(gemini_matcher, "is_gemini_available", lambda: True)

    assert _backfill_score_fn() is gemini_matcher.score_jobs_gemini


def test_backfill_uses_nvidia_when_gemini_breaker_is_open(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")
    monkeypatch.setattr(gemini_matcher, "is_gemini_available", lambda: False)

    assert _backfill_score_fn() is score_jobs_nvidia


def test_backfill_uses_nvidia_when_gemini_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "")

    assert _backfill_score_fn() is score_jobs_nvidia


@pytest.mark.asyncio
async def test_nvidia_idle_rescore_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_idle_rescore_enabled", False)
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")

    assert await _nvidia_idle_rescore() is None


@pytest.mark.asyncio
async def test_backfill_falls_back_to_nvidia_in_same_batch(monkeypatch):
    calls: list[str] = []

    async def gemini_score(*_args):
        calls.append("gemini")
        return []

    gemini_score.__name__ = "score_jobs_gemini"

    async def nvidia_score(*_args, **_kwargs):
        calls.append("nvidia")
        return ["nvidia-score"]

    available = iter([True, False])
    monkeypatch.setattr(gemini_matcher, "is_gemini_available", lambda: next(available))
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")
    monkeypatch.setattr("app.scoring.nvidia_matcher.score_jobs_nvidia", nvidia_score)
    monkeypatch.setattr("app.services.scheduler_service.record_ops_event", AsyncMock())

    result = await _score_backfill_batch(gemini_score, [object()], object(), object())

    assert result == ["nvidia-score"]
    assert calls == ["gemini", "nvidia"]
