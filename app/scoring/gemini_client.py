"""Shared Google GenAI SDK client and generation helpers.

The legacy ``google-generativeai`` package is no longer actively maintained.
Keep all new SDK wiring in one module so scoring, detailed analysis, and
embeddings share a single connection pool and a single migration surface.
"""
from __future__ import annotations

from typing import Any

from app.config import settings

_client: Any | None = None


def get_gemini_client():
    """Return the process-wide ``google.genai.Client`` instance."""
    global _client
    if _client is None:
        from google import genai  # noqa: PLC0415

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def generate_gemini_content(
    prompt: str,
    *,
    model: str,
    max_output_tokens: int,
    response_json_schema: dict[str, Any] | None = None,
):
    """Generate content through the async Google GenAI SDK.

    Gemini 3.5/3.6 deprecate sampling controls such as ``temperature``,
    ``top_p``, and ``top_k``. We intentionally omit them and use explicit
    instructions plus an optional JSON schema for deterministic output.
    """
    config: dict[str, Any] = {"max_output_tokens": max_output_tokens}
    if response_json_schema is not None:
        config.update(
            {
                "response_mime_type": "application/json",
                "response_json_schema": response_json_schema,
            }
        )

    client = get_gemini_client()
    return await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )


async def close_gemini_client() -> None:
    """Release async and sync transports during application shutdown."""
    global _client
    if _client is None:
        return
    try:
        await _client.aio.aclose()
    finally:
        _client.close()
        _client = None
