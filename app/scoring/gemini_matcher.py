"""Gemini scorer — lower-cost alternative to Claude for backfill scoring.

Used by real-time scoring, backfill, and the zero-score recheck pass.
Detailed single-job analysis uses the shared client from ``gemini_client.py``.

Activation: set GEMINI_API_KEY in .env.
Leave it empty to fall back to Claude.

Recheck pass (recheck_zero_scores):
  After main backfill queue is empty, Gemini re-evaluates pre-filter rejects
  (score=0, ai_analysis=NULL) to catch anything the rule-based filter missed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
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
from app.scoring.gemini_client import generate_gemini_content
from app.scoring.matcher import SCORING_PROMPT, build_profile_text
from app.scoring.profile_hash import MODEL_GEMINI, compute_profile_hash
from app.scoring.rules import pre_filter
from app.services.ops_service import record_ops_event

logger = logging.getLogger(__name__)

# Serialise all Gemini calls across the process so the configured pacer applies
# predictably even when several scheduler paths become ready at once.
_gemini_semaphore = asyncio.Semaphore(1)

# Global pacer — enforces the configured minimum interval between requests.
_pacer_lock = asyncio.Lock()
_last_call_monotonic: float = 0.0

# Circuit breaker — trip when daily quota is exhausted so backfill can hand work
# off to NVIDIA instead of looping retries forever. Resets at next UTC midnight.
_breaker_lock = asyncio.Lock()
_gemini_disabled_until: datetime | None = None  # UTC, naive
_consecutive_exhausts: int = 0
_BREAKER_TRIP_THRESHOLD = 3  # exhausted batches in a row

_SCORING_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "job_index": {"type": "integer"},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "breakdown": {
                "type": "object",
                "properties": {
                    "relevance": {"type": "integer", "minimum": 0, "maximum": 100},
                    "seniority": {"type": "integer", "minimum": 0, "maximum": 100},
                    "language_fit": {"type": "integer", "minimum": 0, "maximum": 100},
                    "location": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["relevance", "seniority", "language_fit", "location"],
            },
            "verdict": {"type": "string"},
            "red_flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["job_index", "score", "breakdown", "verdict", "red_flags"],
    },
}


def _next_utc_midnight() -> datetime:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tomorrow = (now + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time())


def is_gemini_available() -> bool:
    """Check the breaker. True if Gemini calls may proceed."""
    global _gemini_disabled_until
    if _gemini_disabled_until is None:
        return True
    if datetime.now(timezone.utc).replace(tzinfo=None) >= _gemini_disabled_until:
        _gemini_disabled_until = None
        return True
    return False


async def _record_success() -> None:
    """Reset the consecutive-exhausts counter on a successful Gemini response."""
    global _consecutive_exhausts
    if _consecutive_exhausts:
        _consecutive_exhausts = 0


async def _record_exhaust(reason: str) -> None:
    """Bump the breaker counter; trip if threshold reached."""
    global _consecutive_exhausts, _gemini_disabled_until
    async with _breaker_lock:
        _consecutive_exhausts += 1
        logger.warning(
            "Gemini exhausted #%d/%d (reason=%s)",
            _consecutive_exhausts, _BREAKER_TRIP_THRESHOLD, reason,
        )
        if _consecutive_exhausts >= _BREAKER_TRIP_THRESHOLD and _gemini_disabled_until is None:
            _gemini_disabled_until = _next_utc_midnight()
            await record_ops_event(
                "gemini_breaker_open",
                "warning",
                source="gemini",
                message=f"disabled_until={_gemini_disabled_until.isoformat()}Z reason={reason}",
            )
            logger.warning(
                "Gemini circuit breaker OPEN until %s UTC", _gemini_disabled_until.isoformat()
            )


async def _pace() -> None:
    """Sleep so the configured interval elapses between Gemini requests."""
    global _last_call_monotonic
    async with _pacer_lock:
        now = time.monotonic()
        elapsed = now - _last_call_monotonic
        if elapsed < settings.gemini_batch_delay:
            await asyncio.sleep(settings.gemini_batch_delay - elapsed)
        _last_call_monotonic = time.monotonic()


def _error_status(exc: BaseException) -> int | None:
    """Extract an HTTP status from both old and new Google SDK exceptions."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if callable(status):
        status = status()
    status = getattr(status, "value", status)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient statuses from both Google SDK generations."""
    if _error_status(exc) in {408, 429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__
    return name in {
        "ResourceExhausted",
        "ServiceUnavailable",
        "DeadlineExceeded",
        "InternalServerError",
        "ReadTimeout",
        "ConnectTimeout",
        "TimeoutError",
    }


# ---------------------------------------------------------------------------
# Low-level: call Gemini, return parsed (job, score, verdict) tuples — no DB
# ---------------------------------------------------------------------------

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


async def _generate_with_retry(prompt: str, batch_size: int):
    """Call Gemini with pacing, single-flight serialisation, and tenacity retry
    on 429/503/timeouts. Exp backoff 5→10→20→40→80s + ±25% jitter, 5 attempts.

    NOTE: ``reraise=False`` is intentional. With ``reraise=True``, tenacity
    re-raises the original ``ResourceExhausted`` after the last attempt —
    which would slip past the ``except RetryError`` handler in
    ``_call_gemini_raw`` and never trigger the circuit breaker. Production
    confirmed this: 82 ``gemini_429`` retries with zero ``gemini_exhausted``
    events in a 2-hour window. Letting tenacity wrap into ``RetryError``
    routes exhaustion through the proper handler.
    """
    attempt_counter = {"n": 0}

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=5, min=5, max=80),
        retry=retry_if_exception(_is_retryable),
        reraise=False,
    ):
        with attempt:
            attempt_counter["n"] += 1
            async with _gemini_semaphore:
                await _pace()
                try:
                    return await generate_gemini_content(
                        prompt,
                        model=settings.gemini_scoring_model,
                        max_output_tokens=settings.gemini_scoring_max_output_tokens,
                        response_json_schema=_SCORING_RESPONSE_SCHEMA,
                    )
                except Exception as exc:
                    if _is_retryable(exc):
                        # Additive jitter so parallel users don't collide
                        jitter = random.uniform(0, 2.0)
                        await asyncio.sleep(jitter)
                        logger.warning(
                            "Gemini transient error (attempt %d, batch=%d): %s",
                            attempt_counter["n"], batch_size, type(exc).__name__,
                        )
                        if _error_status(exc) == 429 or type(exc).__name__ == "ResourceExhausted":
                            await record_ops_event(
                                "gemini_429",
                                "retry",
                                source="gemini",
                                message=f"attempt={attempt_counter['n']} batch={batch_size}",
                            )
                    raise


async def _call_gemini_raw(
    jobs: list[Job],
    profile_text: str,
) -> list[tuple[Job, int, str]]:
    """Call Gemini, return list of (job, score, verdict). No DB interaction."""
    if not is_gemini_available():
        return []

    prompt = SCORING_PROMPT.format(
        profile_text=profile_text,
        jobs_text=_build_jobs_text(jobs),
    )
    try:
        response = await _generate_with_retry(prompt, len(jobs))
        await _record_success()
        results = getattr(response, "parsed", None)
        if not isinstance(results, list):
            text = (response.text or "").strip()

            # Defensive fallback for providers/proxies that ignore the schema.
            if "```" in text:
                if "```json" in text:
                    text = text.split("```json", 1)[-1]
                elif text.count("```") >= 2:
                    text = text.split("```")[1]
                text = text.replace("```", "").strip()
            if not text.endswith("]"):
                last_brace = text.rfind("}")
                if last_brace > 0:
                    text = text[: last_brace + 1] + "]"
            results = json.loads(text)
        if not isinstance(results, list):
            raise ValueError("Gemini scoring response is not a JSON array")
    except RetryError as exc:
        final_exc = exc.last_attempt.exception()
        final_name = type(final_exc).__name__
        logger.error(
            "Gemini retries exhausted (batch_size=%d): %s", len(jobs), final_exc
        )
        await record_ops_event(
            "gemini_exhausted",
            "error",
            source="gemini",
            message=f"batch={len(jobs)} final={final_name}",
        )
        await _record_exhaust(final_name)
        return []
    except Exception as exc:
        logger.error("Gemini API call failed (batch_size=%d): %s", len(jobs), exc)
        # Safety net: any retryable error that escapes tenacity (e.g. due to
        # a future ``reraise=True`` regression) still feeds the breaker so we
        # don't sit in a tight 429 loop forever. The dedicated
        # ``except RetryError`` above is the primary path.
        if _is_retryable(exc):
            await _record_exhaust(type(exc).__name__)
        return []

    output: list[tuple[Job, int, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("job_index", 0))
        if idx < 0 or idx >= len(jobs):
            continue
        output.append((
            jobs[idx],
            min(100, max(0, int(item.get("score", 0)))),
            item.get("verdict", ""),
        ))
    return output


# ---------------------------------------------------------------------------
# Main backfill scorer: INSERT new JobScore rows
# ---------------------------------------------------------------------------

async def score_jobs_gemini(
    jobs: list[Job],
    user: User,
    session: AsyncSession,
) -> list[JobScore]:
    """Score a list of jobs using Gemini Flash.

    Identical contract to ``score_jobs()`` in matcher.py — drop-in swap for backfill.
    """
    profile = user.profile
    if not profile:
        return []

    if not is_gemini_available():
        logger.info("Gemini breaker open — score_jobs_gemini no-op for user %s", user.id)
        return []

    profile_text = build_profile_text(profile)
    profile_hash = compute_profile_hash(profile)
    model_version = MODEL_GEMINI()
    new_scores: list[JobScore] = []

    for i in range(0, len(jobs), settings.max_jobs_per_scoring_batch):
        if not is_gemini_available():
            logger.info("Gemini breaker tripped mid-loop — aborting at batch %d", i)
            break
        if i > 0:
            await asyncio.sleep(settings.gemini_batch_delay)

        batch = jobs[i : i + settings.max_jobs_per_scoring_batch]
        batch_results = await _call_gemini_raw(batch, profile_text)
        if not batch_results:
            continue

        # Insert new scores; on conflict overwrite stale provenance, including
        # legacy rows whose profile_hash is NULL.
        rows = [
            {
                "job_id": job.id,
                "user_id": user.id,
                "score": score,
                "ai_analysis": verdict,
                "profile_hash": profile_hash,
                "model_version": model_version,
            }
            for job, score, verdict in batch_results
        ]
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
            logger.warning(
                "Gemini batch insert failed for user_id=%s: %s", user.id, exc
            )
            continue

        inserted_job_ids = {row.job_id for row in inserted}
        job_map = {job.id: (job, score, verdict) for job, score, verdict in batch_results}
        for jid in inserted_job_ids:
            if jid in job_map:
                job, score, verdict = job_map[jid]
                new_scores.append(JobScore(
                    job_id=jid,
                    user_id=user.id,
                    score=score,
                    ai_analysis=verdict,
                ))

    return new_scores


# ---------------------------------------------------------------------------
# Recheck pass: UPDATE existing score=0 records (pre-filter rejects)
# ---------------------------------------------------------------------------

async def recheck_zero_scores(
    user: User,
    session: AsyncSession,
    limit: int = 500,
) -> tuple[int, int]:
    """Re-evaluate pre-filter rejects with full Gemini AI pass.

    Targets legacy jobs where score=0 AND ai_analysis IS NULL. The current
    profile rules are applied again before AI: an explicit hard reject is
    final and cannot be overridden by a probabilistic model.

    After this pass:
    - Current hard reject → mark as filtered; never send it to Gemini
    - Current pre-filter pass + Gemini score > 0 → record updated
    - Current pre-filter pass + Gemini score = 0 → mark confirmed

    Returns (checked, upgraded) counts.
    """
    profile = user.profile
    if not profile:
        return 0, 0

    if not is_gemini_available():
        logger.info("Gemini breaker open — recheck_zero_scores no-op for user %s", user.id)
        return 0, 0

    # Find pre-filter rejects not yet rechecked (ai_analysis IS NULL = never seen by AI)
    zs_result = await session.execute(
        select(JobScore).where(
            JobScore.user_id == user.id,
            JobScore.score == 0,
            JobScore.ai_analysis.is_(None),
        ).limit(limit)
    )
    zero_scores = zs_result.scalars().all()

    if not zero_scores:
        return 0, 0

    job_ids = [zs.job_id for zs in zero_scores]
    jobs_result = await session.execute(select(Job).where(Job.id.in_(job_ids)))
    jobs_map = {j.id: j for j in jobs_result.scalars().all()}
    score_map = {zs.job_id: zs for zs in zero_scores}

    profile_text = build_profile_text(profile)
    profile_hash = compute_profile_hash(profile)
    model_version = MODEL_GEMINI()

    jobs_list: list[Job] = []
    filtered_count = 0
    for job_id in job_ids:
        job = jobs_map.get(job_id)
        existing = score_map.get(job_id)
        if job is None or existing is None:
            continue
        passed, _bucket = pre_filter(job, profile)
        if passed:
            jobs_list.append(job)
            continue

        existing.ai_analysis = "Filtered by current profile rules"
        existing.scored_at = datetime.now()
        existing.profile_hash = profile_hash
        existing.model_version = "prefilter"
        filtered_count += 1

    if filtered_count:
        await session.commit()
        logger.info(
            "Recheck [user %s]: kept %d current profile-rule rejects at score=0",
            user.telegram_id,
            filtered_count,
        )

    checked = 0
    upgraded = 0

    for i in range(0, len(jobs_list), settings.max_jobs_per_scoring_batch):
        if i > 0:
            await asyncio.sleep(settings.gemini_batch_delay)

        batch = jobs_list[i : i + settings.max_jobs_per_scoring_batch]
        raw = await _call_gemini_raw(batch, profile_text)

        for job, score, verdict in raw:
            existing = score_map.get(job.id)
            if not existing:
                continue
            existing.score = score
            # Non-NULL ai_analysis marks this job as "seen by AI" — won't be rechecked
            existing.ai_analysis = verdict if verdict else "✓ confirmed"
            existing.scored_at = datetime.now()
            existing.profile_hash = profile_hash
            existing.model_version = model_version
            checked += 1
            if score > 0:
                upgraded += 1

        await session.commit()

    if checked:
        logger.info(
            "Recheck [user %s]: %d pre-filter rejects re-evaluated, %d upgraded (score > 0)",
            user.telegram_id, checked, upgraded,
        )

    return checked, upgraded
