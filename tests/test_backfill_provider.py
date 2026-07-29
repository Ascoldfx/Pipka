import pytest

from app.config import settings
from app.scoring import gemini_matcher, nvidia_matcher
from app.scoring.matcher import score_jobs
from app.services.scheduler_service import _backfill_score_fn, _nvidia_idle_rescore


def test_backfill_prefers_gemini_when_both_providers_are_available(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")
    monkeypatch.setattr(gemini_matcher, "is_gemini_available", lambda: True)

    assert _backfill_score_fn() is gemini_matcher.score_jobs_gemini


def test_backfill_uses_nvidia_when_gemini_breaker_is_open(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")
    monkeypatch.setattr(gemini_matcher, "is_gemini_available", lambda: False)

    assert _backfill_score_fn() is nvidia_matcher.score_jobs_nvidia


def test_backfill_uses_claude_when_optional_providers_are_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "nvidia_api_key", "")

    assert _backfill_score_fn() is score_jobs


@pytest.mark.asyncio
async def test_nvidia_idle_rescore_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "nvidia_idle_rescore_enabled", False)
    monkeypatch.setattr(settings, "nvidia_api_key", "nvidia-test")

    assert await _nvidia_idle_rescore() is None
