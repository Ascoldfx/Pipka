from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.profile_hash import compute_profile_hash

logger = logging.getLogger(__name__)

_embed_lock = asyncio.Lock()
_pace_lock = asyncio.Lock()
_last_embed_call: float = 0.0
_genai_configured = False


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _enabled(session: AsyncSession) -> bool:
    return bool(settings.embedding_enabled and settings.gemini_api_key and _is_postgres(session))


async def _pace() -> None:
    global _last_embed_call
    async with _pace_lock:
        elapsed = time.monotonic() - _last_embed_call
        if elapsed < settings.embedding_batch_delay:
            await asyncio.sleep(settings.embedding_batch_delay - elapsed)
        _last_embed_call = time.monotonic()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in values) + "]"


def _normalise_dimension(values: list[float]) -> list[float]:
    dim = settings.embedding_dimension
    if len(values) == dim:
        return values
    if len(values) > dim:
        return values[:dim]
    return values + [0.0] * (dim - len(values))


def _extract_embedding(response: Any) -> list[float]:
    if isinstance(response, dict):
        embedding = response.get("embedding") or response.get("embeddings")
    else:
        embedding = getattr(response, "embedding", None) or getattr(response, "embeddings", None)

    if isinstance(embedding, dict):
        embedding = embedding.get("values")
    if embedding and isinstance(embedding[0], dict):
        embedding = embedding[0].get("values")
    if embedding and isinstance(embedding[0], list):
        embedding = embedding[0]
    if not embedding:
        raise ValueError("Embedding response did not contain values")
    return _normalise_dimension([float(v) for v in embedding])


def _embed_sync(text_value: str, *, task_type: str) -> list[float]:
    global _genai_configured
    import google.generativeai as genai  # noqa: PLC0415

    if not _genai_configured:
        genai.configure(api_key=settings.gemini_api_key)
        _genai_configured = True

    kwargs = {
        "model": settings.embedding_model,
        "content": text_value,
        "task_type": task_type,
    }
    if settings.embedding_dimension:
        kwargs["output_dimensionality"] = settings.embedding_dimension

    try:
        response = genai.embed_content(**kwargs)
    except TypeError:
        # Older google-generativeai releases do not expose output_dimensionality.
        kwargs.pop("output_dimensionality", None)
        response = genai.embed_content(**kwargs)
    return _extract_embedding(response)


async def embed_text(text_value: str, *, task_type: str) -> list[float]:
    await _pace()
    text_value = (text_value or "").strip()
    if not text_value:
        raise ValueError("Cannot embed empty text")
    return await asyncio.to_thread(_embed_sync, text_value[:12000], task_type=task_type)


def build_job_embedding_text(job: Job) -> str:
    parts = [
        f"Title: {job.title or ''}",
        f"Company: {job.company_name or ''}",
        f"Location: {job.location or ''} ({job.country or ''})",
    ]
    if job.description:
        parts.append(f"Description: {job.description[:6000]}")
    return "\n".join(parts)


def build_profile_embedding_text(profile: UserProfile) -> str:
    from app.scoring.matcher import build_profile_text  # noqa: PLC0415

    return build_profile_text(profile)


async def invalidate_profile_embedding(session: AsyncSession, profile_id: int) -> None:
    if not _is_postgres(session):
        return
    await session.execute(
        text(
            """
            UPDATE user_profiles
            SET embedding = NULL,
                embedding_model = NULL,
                embedding_updated_at = NULL,
                embedding_profile_hash = NULL
            WHERE id = :profile_id
            """
        ),
        {"profile_id": profile_id},
    )


async def index_missing_embeddings(session: AsyncSession) -> dict[str, int]:
    """Fill missing/stale job and profile embeddings.

    This is intentionally small-batch and scheduler-friendly: it never blocks
    the core scan/scoring path, and it no-ops outside PostgreSQL/Gemini setups.
    """
    if not _enabled(session):
        return {"jobs": 0, "profiles": 0, "skipped": 1}

    if _embed_lock.locked():
        return {"jobs": 0, "profiles": 0, "skipped": 1}

    async with _embed_lock:
        indexed_jobs = await _index_jobs(session)
        indexed_profiles = await _index_profiles(session)
        return {"jobs": indexed_jobs, "profiles": indexed_profiles, "skipped": 0}


async def _index_jobs(session: AsyncSession) -> int:
    result = await session.execute(
        select(Job)
        .where(text("jobs.embedding IS NULL"))
        .order_by(Job.scraped_at.desc())
        .limit(settings.embedding_jobs_per_run)
    )
    jobs = list(result.scalars())
    indexed = 0
    for job in jobs:
        try:
            embedding = await embed_text(build_job_embedding_text(job), task_type="retrieval_document")
            await session.execute(
                text(
                    """
                    UPDATE jobs
                    SET embedding = CAST(:embedding AS vector),
                        embedding_model = :model,
                        embedding_updated_at = :updated_at
                    WHERE id = :job_id
                    """
                ),
                {
                    "job_id": job.id,
                    "embedding": _vector_literal(embedding),
                    "model": settings.embedding_model,
                    "updated_at": datetime.now(),
                },
            )
            indexed += 1
        except Exception as exc:
            logger.warning("Job embedding failed job_id=%s: %s", job.id, exc)
    await session.commit()
    return indexed


async def _index_profiles(session: AsyncSession) -> int:
    result = await session.execute(
        select(UserProfile)
        .options(selectinload(UserProfile.user))
        .order_by(UserProfile.updated_at.desc())
        .limit(200)
    )
    profiles = list(result.scalars())
    indexed = 0

    for profile in profiles:
        current_hash = compute_profile_hash(profile)
        row = await session.execute(
            text("SELECT embedding_profile_hash FROM user_profiles WHERE id = :profile_id"),
            {"profile_id": profile.id},
        )
        existing_hash = row.scalar_one_or_none()
        if existing_hash == current_hash:
            continue
        if indexed >= settings.embedding_profiles_per_run:
            break
        try:
            embedding = await embed_text(build_profile_embedding_text(profile), task_type="retrieval_query")
            await session.execute(
                text(
                    """
                    UPDATE user_profiles
                    SET embedding = CAST(:embedding AS vector),
                        embedding_model = :model,
                        embedding_updated_at = :updated_at,
                        embedding_profile_hash = :profile_hash
                    WHERE id = :profile_id
                    """
                ),
                {
                    "profile_id": profile.id,
                    "embedding": _vector_literal(embedding),
                    "model": settings.embedding_model,
                    "updated_at": datetime.now(),
                    "profile_hash": current_hash,
                },
            )
            indexed += 1
        except Exception as exc:
            logger.warning("Profile embedding failed profile_id=%s: %s", profile.id, exc)

    await session.commit()
    return indexed


async def semantic_job_ids_for_profile(
    session: AsyncSession,
    *,
    user_id: int,
    limit: int | None = None,
    include_closed: bool = False,
) -> list[int]:
    if not _enabled(session):
        return []

    closed_filter = "" if include_closed else "AND (j.url_status IS NULL OR j.url_status != 'closed')"
    result = await session.execute(
        text(
            f"""
            SELECT j.id
            FROM jobs j
            JOIN user_profiles p ON p.user_id = :user_id
            WHERE j.embedding IS NOT NULL
              AND p.embedding IS NOT NULL
              {closed_filter}
            ORDER BY j.embedding <=> p.embedding
            LIMIT :limit
            """
        ),
        {"user_id": user_id, "limit": limit or settings.semantic_search_limit},
    )
    return [int(row[0]) for row in result.fetchall()]
