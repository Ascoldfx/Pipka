import pytest

from app.config import settings
from app.models.job import Job
from app.models.user import User, UserProfile
from app.scoring import nvidia_matcher
from app.scoring.nvidia_matcher import _parse_scores


def _jobs(n: int = 2) -> list[Job]:
    return [Job(id=i + 1, title=f"Director {i}") for i in range(n)]


def test_trailing_commentary_after_array_is_ignored():
    raw = (
        '[{"job_index": 0, "score": 81, "verdict": "ok"}]\n'
        'Note: {"job_index": 1} was ambiguous.'
    )
    result = _parse_scores(raw, _jobs())
    assert [(job.id, score) for job, score, _ in result] == [(1, 81)]


def test_fenced_array_is_parsed():
    raw = '```json\n[{"job_index": 1, "score": 40, "verdict": "weak"}]\n```'
    result = _parse_scores(raw, _jobs())
    assert [(job.id, score) for job, score, _ in result] == [(2, 40)]


def test_truncated_array_keeps_complete_objects():
    raw = '[{"job_index": 0, "score": 70, "verdict": "a"}, {"job_index": 1, "sco'
    result = _parse_scores(raw, _jobs())
    assert [(job.id, score) for job, score, _ in result] == [(1, 70)]


def test_single_object_is_accepted():
    raw = '{"job_index": 0, "score": 55, "verdict": "mid"}'
    result = _parse_scores(raw, _jobs())
    assert [(job.id, score) for job, score, _ in result] == [(1, 55)]


def test_garbage_returns_empty():
    assert _parse_scores("model is overloaded, try later", _jobs()) == []


@pytest.mark.asyncio
async def test_expired_deadline_stops_before_any_request(monkeypatch):
    calls: list[int] = []

    async def fake_score_batch(batch, _profile_text):
        calls.append(len(batch))
        return []

    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(nvidia_matcher, "_score_batch", fake_score_batch)
    user = User(id=1, profile=UserProfile(target_titles=["Director"]))

    result = await nvidia_matcher.score_jobs_nvidia(_jobs(3), user, session=None, deadline=0.0)

    assert result == []
    assert calls == []
