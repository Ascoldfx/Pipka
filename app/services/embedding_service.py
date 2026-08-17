from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring.gemini_client import get_gemini_client
from app.scoring.nvidia_embedding_client import embed_nvidia_text
from app.scoring.profile_hash import compute_profile_hash
from app.services.user_scope_service import ActiveTargetScope, active_target_scope

logger = logging.getLogger(__name__)

_embed_lock = asyncio.Lock()
_pace_lock = asyncio.Lock()
_last_embed_call: float = 0.0


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _enabled(session: AsyncSession) -> bool:
    provider = settings.embedding_provider.lower()
    has_provider_key = (
        settings.nvidia_api_key if provider == "nvidia" else settings.gemini_api_key
    )
    return bool(
        settings.embedding_enabled
        and provider in {"gemini", "nvidia"}
        and has_provider_key
        and _is_postgres(session)
    )


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
    if settings.embedding_provider.lower() == "nvidia" and len(values) != dim:
        raise ValueError(f"NVIDIA embedding dimension {len(values)} != configured {dim}")
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
    elif hasattr(embedding, "values") and not callable(embedding.values):
        embedding = embedding.values
    if isinstance(embedding, (list, tuple)) and embedding:
        first = embedding[0]
        if isinstance(first, dict):
            embedding = first.get("values")
        elif hasattr(first, "values") and not callable(first.values):
            embedding = first.values
        elif isinstance(first, (list, tuple)):
            embedding = first
    if not embedding:
        raise ValueError("Embedding response did not contain values")
    return _normalise_dimension([float(v) for v in embedding])


async def _embed(text_value: str, *, task_type: str) -> list[float]:
    if settings.embedding_provider.lower() == "nvidia":
        return _normalise_dimension(await embed_nvidia_text(text_value, task_type=task_type))

    config: dict[str, Any] = {"task_type": task_type.upper()}
    if settings.embedding_dimension:
        config["output_dimensionality"] = settings.embedding_dimension
    response = await get_gemini_client().aio.models.embed_content(
        model=settings.embedding_model,
        contents=text_value,
        config=config,
    )
    return _extract_embedding(response)


async def embed_text(text_value: str, *, task_type: str) -> list[float]:
    await _pace()
    text_value = (text_value or "").strip()
    if not text_value:
        raise ValueError("Cannot embed empty text")
    return await _embed(text_value[:12000], task_type=task_type)


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


def job_index_filters(
    cutoff: datetime,
    *,
    countries: tuple[str, ...] | None = None,
    user_ids: tuple[int, ...] | None = None,
):
    """Return the shared scope for useful, unembedded vacancy vectors."""
    if countries is None:
        countries = (settings.embedding_index_country.strip().lower(),)
    posted_or_scraped = func.coalesce(Job.posted_at, Job.scraped_at)
    score_conditions = [
        JobScore.job_id == Job.id,
        JobScore.score >= settings.embedding_index_min_score,
    ]
    if user_ids is not None:
        score_conditions.append(JobScore.user_id.in_(user_ids))
    has_strong_score = select(JobScore.id).where(*score_conditions).exists()
    return (
        text("jobs.embedding IS NULL"),
        Job.country.in_(countries),
        posted_or_scraped >= cutoff,
        or_(Job.url_status.is_(None), Job.url_status == "active"),
        has_strong_score,
    )


async def _count_indexable_jobs(
    session: AsyncSession,
    cutoff: datetime,
    scope: ActiveTargetScope | None = None,
) -> int:
    scope = scope or await active_target_scope(session)
    result = await session.execute(
        select(func.count(Job.id)).where(
            *job_index_filters(
                cutoff, countries=scope.countries, user_ids=scope.user_ids
            )
        )
    )
    return int(result.scalar_one())


async def index_missing_embeddings(
    session: AsyncSession,
    *,
    min_pending_jobs: int | None = None,
    include_profiles: bool = True,
) -> dict[str, int]:
    """Fill missing/stale job and profile embeddings.

    This is intentionally small-batch and scheduler-friendly: it never blocks
    the core scan/scoring path, and it no-ops outside PostgreSQL/provider setups.

    ``min_pending_jobs`` makes the NVIDIA burst worker conditional: it runs
    only when the scoped vacancy queue is strictly larger than the threshold.
    """
    if not _enabled(session):
        return {"jobs": 0, "profiles": 0, "skipped": 1}

    if _embed_lock.locked():
        return {"jobs": 0, "profiles": 0, "skipped": 1}

    async with _embed_lock:
        cutoff = datetime.now() - timedelta(days=settings.embedding_index_max_age_days)
        scope = await active_target_scope(session)
        if min_pending_jobs is not None:
            pending_jobs = await _count_indexable_jobs(session, cutoff, scope)
            if pending_jobs <= min_pending_jobs:
                return {
                    "jobs": 0,
                    "profiles": 0,
                    "pending": pending_jobs,
                    "skipped": 1,
                }
        else:
            pending_jobs = -1

        indexed_jobs = await _index_jobs(session, cutoff=cutoff, scope=scope)
        indexed_profiles = await _index_profiles(session) if include_profiles else 0
        return {
            "jobs": indexed_jobs,
            "profiles": indexed_profiles,
            "pending": pending_jobs,
            "skipped": 0,
        }


async def _index_jobs(
    session: AsyncSession,
    *,
    cutoff: datetime | None = None,
    scope: ActiveTargetScope | None = None,
) -> int:
    cutoff = cutoff or datetime.now() - timedelta(days=settings.embedding_index_max_age_days)
    scope = scope or await active_target_scope(session)
    posted_or_scraped = func.coalesce(Job.posted_at, Job.scraped_at)
    result = await session.execute(
        select(Job)
        .where(
            *job_index_filters(
                cutoff, countries=scope.countries, user_ids=scope.user_ids
            )
        )
        .order_by(posted_or_scraped.desc())
        .limit(settings.embedding_jobs_per_run)
    )
    jobs = list(result.scalars())
    # Commit immediately to release the transaction after SELECT
    await session.commit()

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
            # Commit after each job update to release locks quickly
            await session.commit()
            indexed += 1
        except Exception as exc:
            await session.rollback()
            logger.warning("Job embedding failed job_id=%s: %s", job.id, exc)
    return indexed


async def _index_profiles(session: AsyncSession) -> int:
    result = await session.execute(
        select(UserProfile)
        .join(User, User.id == UserProfile.user_id)
        .where(User.is_active.is_(True))
        .options(selectinload(UserProfile.user))
        .order_by(UserProfile.updated_at.desc())
        .limit(200)
    )
    profiles = list(result.scalars())
    # Commit immediately to release the transaction after SELECT
    await session.commit()
    
    indexed = 0
    for profile in profiles:
        current_hash = compute_profile_hash(profile)
        row = await session.execute(
            text("SELECT embedding_profile_hash FROM user_profiles WHERE id = :profile_id"),
            {"profile_id": profile.id},
        )
        existing_hash = row.scalar_one_or_none()
        # Commit SELECT hash check immediately
        await session.commit()
        
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
            # Commit after each profile update to release locks quickly
            await session.commit()
            indexed += 1
        except Exception as exc:
            await session.rollback()
            logger.warning("Profile embedding failed profile_id=%s: %s", profile.id, exc)

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
