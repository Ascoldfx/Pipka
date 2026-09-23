"""NVIDIA Build API scorer — fallback and idle rescorer for active markets.

Runs only when the Gemini backfill queue is drained. Two priorities per pass:
  (a) recheck pre-filter rejects (score=0, ai_analysis IS NULL)
  (b) refresh stale successful scores (score > 0, scored_at older than N days)

Activation: set NVIDIA_API_KEY in .env. Leave empty to disable.
Endpoint: OpenAI-compatible chat completions at nvidia_base_url.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import SCORING_PROMPT, build_profile_text, validated_job_index
from app.scoring.profile_hash import MODEL_NVIDIA, compute_profile_hash
from app.services.ops_service import record_ops_event

logger = logging.getLogger(__name__)

class NvidiaEmptyResponse(Exception):
    """HTTP 200 stream that carried no assistant content (provider hiccup)."""


# Serialise + pace NVIDIA calls, same pattern as gemini_matcher.
_nvidia_semaphore = asyncio.Semaphore(1)
_pacer_lock = asyncio.Lock()
_last_call_monotonic: float = 0.0


def _nvidia_batch_size() -> int:
    """Return the bounded NVIDIA chat batch size used by every scoring path."""
    return max(
        1,
        min(
            settings.max_jobs_per_scoring_batch,
            settings.nvidia_scoring_batch_size,
        ),
    )


def _nvidia_generation_options() -> dict[str, object]:
    """Return bounded generation settings for the configured hosted model."""

    options: dict[str, object] = {
        "max_tokens": settings.nvidia_scoring_max_tokens,
        "temperature": 0.3,
        "top_p": 0.95,
        "stream": True,
    }
    if settings.nvidia_model == "openai/gpt-oss-20b":
        options["reasoning_effort"] = settings.nvidia_scoring_reasoning_effort
    return options


async def _pace() -> None:
    global _last_call_monotonic
    async with _pacer_lock:
        now = time.monotonic()
        elapsed = now - _last_call_monotonic
        if elapsed < settings.nvidia_batch_delay:
            await asyncio.sleep(settings.nvidia_batch_delay - elapsed)
        _last_call_monotonic = time.monotonic()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    if isinstance(
        exc,
        (
            NvidiaEmptyResponse,
            TimeoutError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ),
    ):
        return True
    return False


def _stream_content(line: str) -> str | None:
    """Extract visible assistant content from one OpenAI-compatible SSE line."""
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    choice = choices[0]
    content = (choice.get("delta") or {}).get("content")
    if content is None:
        content = (choice.get("message") or {}).get("content")
    return content if isinstance(content, str) and content else None


def _build_jobs_text(jobs: list[Job]) -> str:
    jobs_text = ""
    for idx, job in enumerate(jobs):
        desc_preview = (job.description or "")[:1200]
        remote_info = f"Remote: {'Yes' if job.is_remote else 'No' if job.is_remote is False else 'Unknown'}"
        jobs_text += (
            f"\n### Job {idx}\n"
            f"Title: {job.title}\n"
            f"Company: {job.company_name or 'N/A'}\n"
            f"Location: {job.location or 'N/A'} ({job.country or 'N/A'})\n"
            f"{remote_info}\n"
            f"Description: {desc_preview}\n"
        )
    return jobs_text


async def _call_nvidia(prompt: str, batch_size: int) -> str | None:
    """Call NVIDIA chat completions, return raw content or None on failure."""
    url = f"{settings.nvidia_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.nvidia_model,
        "messages": [{"role": "user", "content": prompt}],
        **_nvidia_generation_options(),
    }

    async def _once() -> str | None:
        async with _nvidia_semaphore:
            await _pace()
            async with httpx.AsyncClient(timeout=settings.nvidia_scoring_timeout_seconds) as client:
                # Streaming keeps the read socket active while the hosted model
                # generates a long JSON answer. A total wall-clock guard still
                # prevents a provider that drips tokens from holding the queue.
                async with asyncio.timeout(settings.nvidia_scoring_timeout_seconds * 2):
                    async with client.stream("POST", url, headers=headers, json=payload) as resp:
                        resp.raise_for_status()
                        chunks: list[str] = []
                        async for line in resp.aiter_lines():
                            content = _stream_content(line)
                            if content:
                                chunks.append(content)
                        content_text = "".join(chunks)
                        if not content_text:
                            # Previously returned None silently: no log, no
                            # OpsEvent, invisible in Ops. Surface it as a
                            # transient failure so it is counted as exhausted.
                            raise NvidiaEmptyResponse("empty stream")
                        return content_text

    # Laguna XS typically responds in under a minute for one vacancy. Larger
    # batches are rejected or time out on the hosted free endpoint.
    # Retries handle transient ReadTimeouts (cold starts). Per-attempt noise is
    # logged at DEBUG; one WARNING summarises an exhausted batch below. 429s go
    # to OpsEvent because they're rare and quota-meaningful.
    last_status: str = "?"
    last_exc_name: str = ""
    attempt_n: int = 0
    try:
        # reraise=False is load-bearing: with reraise=True tenacity re-raises the
        # original exception (e.g. httpx.ReadTimeout, whose str() is empty) instead
        # of RetryError, so the ``except RetryError`` below never fired and
        # nvidia_exhausted OpsEvents were silently lost. Same bug class as the
        # Gemini breaker fix (27.05.2026).
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, settings.nvidia_scoring_max_attempts)),
            wait=wait_exponential(multiplier=2, min=2, max=15),
            retry=retry_if_exception(_is_retryable),
            reraise=False,
        ):
            with attempt:
                attempt_n += 1
                try:
                    return await _once()
                except Exception as exc:
                    if _is_retryable(exc):
                        await asyncio.sleep(random.uniform(0, 1.5))
                        last_status = str(getattr(getattr(exc, "response", None), "status_code", "?"))
                        last_exc_name = type(exc).__name__
                        logger.debug(
                            "NVIDIA transient (attempt=%d batch=%d status=%s): %s",
                            attempt_n, batch_size, last_status, last_exc_name,
                        )
                        if last_status == "429":
                            await record_ops_event(
                                "nvidia_429", "retry", source="nvidia",
                                message=f"batch={batch_size}",
                            )
                    raise
    except RetryError:
        # Single summary at WARNING for the whole batch — visible but quiet.
        logger.warning(
            "NVIDIA exhausted after %d retries (batch=%d last_status=%s): %s",
            attempt_n, batch_size, last_status, last_exc_name,
        )
        await record_ops_event(
            "nvidia_exhausted", "error", source="nvidia",
            message=f"batch={batch_size} attempts={attempt_n} last_status={last_status} {last_exc_name}",
        )
        return None
    except Exception as exc:
        # Include the type name — httpx timeout exceptions stringify to "".
        logger.error("NVIDIA call failed (batch=%d): %s %s", batch_size, type(exc).__name__, exc)
        return None
    return None


def _parse_scores(raw: str, jobs: list[Job]) -> list[tuple[Job, int, str]]:
    """Parse JSON array response. Gemma often wraps in ```json fences."""
    text = raw.strip()
    if "```" in text:
        if "```json" in text:
            text = text.split("```json", 1)[-1]
        elif text.count("```") >= 2:
            text = text.split("```")[1]
        text = text.replace("```", "").strip()

    # Models sometimes append commentary after a complete array. Decode only
    # the first JSON value and ignore the tail; fall back to repairing a
    # truncated array only when that fails.
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    start = min(starts) if starts else -1
    try:
        if start < 0:
            raise json.JSONDecodeError("no JSON value", text, 0)
        results, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        repaired = text[start:] if start >= 0 else text
        last_brace = repaired.rfind("}")
        if last_brace > 0:
            repaired = repaired[: last_brace + 1] + "]"
        try:
            results = json.loads(repaired)
        except json.JSONDecodeError as exc:
            logger.warning("NVIDIA JSON parse failed: %s | raw[:200]=%s", exc, text[:200])
            return []
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        logger.warning("NVIDIA JSON parse failed: expected array | raw[:200]=%s", text[:200])
        return []

    output: list[tuple[Job, int, str]] = []
    for item in results:
        idx = validated_job_index(item, len(jobs))
        if idx is None:
            continue
        output.append((
            jobs[idx],
            min(100, max(0, int(item.get("score", 0)))),
            item.get("verdict", ""),
        ))
    return output


async def _score_batch(
    jobs: list[Job],
    profile_text: str,
) -> list[tuple[Job, int, str]]:
    prompt = SCORING_PROMPT.format(profile_text=profile_text, jobs_text=_build_jobs_text(jobs))
    raw = await _call_nvidia(prompt, len(jobs))
    if not raw:
        return []
    return _parse_scores(raw, jobs)


# ---------------------------------------------------------------------------
# Public entry: idle rescore pass for one user
# ---------------------------------------------------------------------------

async def score_jobs_nvidia(
    jobs: list[Job],
    user: User,
    session: AsyncSession,
    deadline: float | None = None,
) -> list[JobScore]:
    """Backfill scorer via NVIDIA Build — drop-in replacement for ``score_jobs_gemini``.

    Used when Gemini circuit breaker is open (daily quota exhausted). Country filter
    is NOT applied here — that's only for the idle rescorer. Backfill via NVIDIA
    runs across whatever jobs the caller hands in.
    """
    profile = user.profile
    if not profile:
        return []
    if not settings.nvidia_api_key:
        return []

    profile_text = build_profile_text(profile)
    profile_hash = compute_profile_hash(profile)
    model_version = MODEL_NVIDIA()
    new_scores: list[JobScore] = []
    batch_size = _nvidia_batch_size()

    for i in range(0, len(jobs), batch_size):
        # ``deadline`` (time.monotonic) bounds callers that must not stall,
        # e.g. the hourly scan. Scored jobs are already committed; the rest
        # stay unscored and are picked up by backfill.
        if deadline is not None and time.monotonic() >= deadline:
            logger.info("NVIDIA scoring time budget reached after %d/%d jobs", i, len(jobs))
            break
        batch = jobs[i : i + batch_size]
        batch_results = await _score_batch(batch, profile_text)
        if not batch_results:
            continue

        rows = [
            {
                "job_id": job.id,
                "user_id": user.id,
                "score": score,
                "ai_analysis": verdict or "✓ confirmed (NVIDIA)",
                "profile_hash": profile_hash,
                "model_version": model_version,
            }
            for job, score, verdict in batch_results
        ]
        # UPSERT stale rows, including legacy rows whose profile_hash is NULL.
        stmt = pg_insert(JobScore).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_id", "user_id"],
            set_={
                "score": stmt.excluded.score,
                "ai_analysis": stmt.excluded.ai_analysis,
                "scored_at": datetime.now(),
                "profile_hash": stmt.excluded.profile_hash,
                "model_version": stmt.excluded.model_version,
            },
            where=or_(
                JobScore.profile_hash.is_(None),
                JobScore.profile_hash != stmt.excluded.profile_hash,
                JobScore.model_version.is_(None),
                JobScore.model_version != stmt.excluded.model_version,
            ),
        ).returning(JobScore.id, JobScore.job_id)
        try:
            inserted = (await session.execute(stmt)).all()
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("NVIDIA batch insert failed for user_id=%s: %s", user.id, exc)
            continue

        inserted_ids = {row.job_id for row in inserted}
        job_map = {job.id: (job, score, verdict) for job, score, verdict in batch_results}
        for jid in inserted_ids:
            job, score, verdict = job_map[jid]
            new_scores.append(JobScore(
                job_id=jid, user_id=user.id, score=score,
                ai_analysis=verdict or "✓ confirmed (NVIDIA)",
            ))

    return new_scores


async def idle_rescore_for_user(
    user: User,
    session: AsyncSession,
    *,
    countries: tuple[str, ...] | None = None,
) -> tuple[int, int, int]:
    """Two-phase rescore for one user's active markets (≤45 days).

    Returns (checked, upgraded, refreshed):
      checked   — priority (a): pre-filter rejects re-evaluated
      upgraded  — subset of `checked` that now has score > 0
      refreshed — priority (b): stale successful scores re-rated
    """
    profile = user.profile
    if not profile:
        return 0, 0, 0

    profile_text = build_profile_text(profile)
    profile_hash = compute_profile_hash(profile)
    model_version = MODEL_NVIDIA()
    budget = settings.nvidia_max_per_run
    batch_size = _nvidia_batch_size()
    countries = countries or (settings.nvidia_country.lower(),)
    age_cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)
    stale_cutoff = datetime.now() - timedelta(days=settings.nvidia_rescore_stale_days)

    # ── Priority (a): pre-filter rejects (score=0, ai_analysis IS NULL) ──
    checked = 0
    upgraded = 0
    a_result = await session.execute(
        select(JobScore, Job)
        .join(Job, JobScore.job_id == Job.id)
        .where(
            JobScore.user_id == user.id,
            JobScore.score == 0,
            JobScore.ai_analysis.is_(None),
            Job.country.in_(countries),
            Job.scraped_at >= age_cutoff,
        )
        .order_by(
            func.coalesce(Job.posted_at, Job.scraped_at).desc().nulls_last(),
            Job.scraped_at.desc().nulls_last(),
            Job.id.desc(),
        )
        .limit(budget)
    )
    a_rows = a_result.all()
    score_map = {js.job_id: js for js, _ in a_rows}
    a_jobs = [j for _, j in a_rows]

    for i in range(0, len(a_jobs), batch_size):
        if budget <= 0:
            break
        batch = a_jobs[i : i + batch_size][:budget]
        batch_results = await _score_batch(batch, profile_text)
        for job, score, verdict in batch_results:
            js = score_map.get(job.id)
            if not js:
                continue
            js.score = score
            js.ai_analysis = verdict if verdict else "✓ confirmed (NVIDIA)"
            js.scored_at = datetime.now()
            js.profile_hash = profile_hash
            js.model_version = model_version
            checked += 1
            if score > 0:
                upgraded += 1
        await session.commit()
        budget -= len(batch)

    # ── Priority (b): stale successful scores — refresh ──
    refreshed = 0
    if budget > 0:
        b_result = await session.execute(
            select(JobScore, Job)
            .join(Job, JobScore.job_id == Job.id)
            .where(
                JobScore.user_id == user.id,
                JobScore.score > 0,
                JobScore.scored_at < stale_cutoff,
                Job.country.in_(countries),
                Job.scraped_at >= age_cutoff,
            )
            .order_by(JobScore.scored_at.asc())
            .limit(budget)
        )
        b_rows = b_result.all()
        b_map = {js.job_id: js for js, _ in b_rows}
        b_jobs = [j for _, j in b_rows]

        for i in range(0, len(b_jobs), batch_size):
            if budget <= 0:
                break
            batch = b_jobs[i : i + batch_size][:budget]
            batch_results = await _score_batch(batch, profile_text)
            for job, score, verdict in batch_results:
                js = b_map.get(job.id)
                if not js:
                    continue
                js.score = score
                js.ai_analysis = verdict if verdict else js.ai_analysis
                js.scored_at = datetime.now()
                js.profile_hash = profile_hash
                js.model_version = model_version
                refreshed += 1
            await session.commit()
            budget -= len(batch)

    if checked or refreshed:
        logger.info(
            "NVIDIA idle rescore [user %s]: checked=%d upgraded=%d refreshed=%d",
            user.telegram_id, checked, upgraded, refreshed,
        )

    return checked, upgraded, refreshed
