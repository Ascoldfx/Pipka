import pytest

from app.config import settings
from app.scoring import nvidia_embedding_client
from app.services import embedding_service


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}


class _Client:
    def __init__(self):
        self.kwargs = None

    async def post(self, _url, **kwargs):
        self.kwargs = kwargs
        return _Response()


@pytest.mark.asyncio
async def test_nemotron_uses_passage_and_query_modes(monkeypatch):
    client = _Client()
    monkeypatch.setattr(nvidia_embedding_client, "_get_client", lambda: client)
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_model", "nvidia/nemotron-3-embed-1b")

    assert await nvidia_embedding_client.embed_nvidia_text("job", task_type="retrieval_document") == [0.1, 0.2, 0.3]
    assert client.kwargs["json"]["input_type"] == "passage"

    await nvidia_embedding_client.embed_nvidia_text("profile", task_type="retrieval_query")
    assert client.kwargs["json"]["input_type"] == "query"


@pytest.mark.asyncio
async def test_nemotron_dimension_must_match_index(monkeypatch):
    async def embedding(*_args, **_kwargs):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(settings, "embedding_provider", "nvidia")
    monkeypatch.setattr(settings, "embedding_dimension", 2048)
    monkeypatch.setattr(embedding_service, "embed_nvidia_text", embedding)

    with pytest.raises(ValueError, match="dimension"):
        await embedding_service._embed("profile", task_type="retrieval_query")
