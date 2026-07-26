import pytest

from app.scoring import gemini_client


@pytest.mark.asyncio
async def test_generation_uses_schema_without_deprecated_sampling_controls(monkeypatch):
    captured = {}

    class FakeModels:
        async def generate_content(self, **kwargs):
            captured.update(kwargs)
            return object()

    class FakeClient:
        class Aio:
            models = FakeModels()

        aio = Aio()

    monkeypatch.setattr(gemini_client, "get_gemini_client", lambda: FakeClient())
    schema = {"type": "array", "items": {"type": "object"}}

    await gemini_client.generate_gemini_content(
        "score these jobs",
        model="gemini-3.5-flash-lite",
        max_output_tokens=12000,
        response_json_schema=schema,
    )

    assert captured["model"] == "gemini-3.5-flash-lite"
    assert captured["config"]["response_json_schema"] == schema
    assert captured["config"]["response_mime_type"] == "application/json"
    assert "temperature" not in captured["config"]
    assert "top_p" not in captured["config"]
    assert "top_k" not in captured["config"]
