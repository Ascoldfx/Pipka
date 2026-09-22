from app.config import settings
from app.scoring.nvidia_matcher import _nvidia_batch_size, _nvidia_generation_options


def test_nvidia_scoring_uses_smaller_batches_than_gemini(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_jobs_per_scoring_batch", 15)
    monkeypatch.setattr(settings, "nvidia_scoring_batch_size", 8)

    assert _nvidia_batch_size() == 8


def test_nvidia_scoring_batch_is_always_positive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_jobs_per_scoring_batch", 0)
    monkeypatch.setattr(settings, "nvidia_scoring_batch_size", 0)

    assert _nvidia_batch_size() == 1


def test_nvidia_reliability_defaults_bound_slow_retries() -> None:
    assert settings.nvidia_model == "openai/gpt-oss-20b"
    assert settings.nvidia_scoring_batch_size == 4
    assert settings.nvidia_scoring_timeout_seconds == 120.0
    assert settings.nvidia_scoring_max_attempts == 2
    assert _nvidia_generation_options() == {
        "max_tokens": 1536,
        "temperature": 0.3,
        "top_p": 0.95,
        "stream": False,
        "reasoning_effort": "low",
    }
