"""Small async client for NVIDIA's OpenAI-compatible embeddings endpoint."""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.nvidia_embedding_timeout_seconds)
    return _client


async def embed_nvidia_text(text_value: str, *, task_type: str) -> list[float]:
    """Embed a vacancy (``passage``) or profile (``query``) with Nemotron."""
    input_type = "passage" if task_type == "retrieval_document" else "query"
    response = await _get_client().post(
        f"{settings.nvidia_base_url.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
        json={
            "model": settings.embedding_model,
            "input": text_value,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        },
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    try:
        values = payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("NVIDIA embedding response did not contain values") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("NVIDIA embedding response was empty")
    return [float(value) for value in values]


async def close_nvidia_embedding_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
