"""NVIDIA Build API scorer — idle rescorer for Germany-only jobs.

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
from sqlalchemy import select
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
from app.scoring.matcher import SCORING_PROMPT, build_profile_text
from app.services.ops_service import record_ops_event

logger = logging.getLogger(__name__)

# Serialise + pace NVIDIA calls, same pattern as gemini_matcher.
_nvidia_semaphore = asyncio.Semaphore(1)
_pacer_lock = asyncio.Lock()
_last_call_monotonic: float = 0.0


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
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return True
    return False


def _build_jobs_text(jobs: list[Job]) -> str:
    jobs_text = ""
    for idx, job in enumerate(jobs):
        desc_preview = (job.description or "")[:1200]
        salary_info = ""
        if job.salary_min or job.salary_max:
            salary_info = f"Salary: {job.salary_min or '?'}-{job.salary_max or '?'} {job.salary_currency or 'EUR'}"
        remote_info = f"Remote: {'Yes' if job.is_remote else 'No' if job.is_remote is False else 'Unknown'}"
        jobs_text += (
            f"\n### Job {idx}\n"
            f"Title: {job.title}\n"
            f"Company: {job.company_name or 'N/A'}\n"
            f"Location: {job.location or 'N/A'} ({job.country or 'N/A'})\n"
            f"{salary_info}\n{remote_info}\n"
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
        "max_tokens": 8000,
        "temperature": 0.3,
        "top_p": 0.95,
        "stream": False,
    }

    async def _once() -> str:
        async with _nvidia_semaphore:
            await _pace()
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=3, min=3, max=60),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                try:
                    return await _once()
                except Exception as exc:
                    if _is_retryable(exc):
                        await asyncio.sleep(random.uniform(0, 1.5))
                        status = getattr(getattr(exc, "response", None), "status_code", "?")
                        logger.warning(
                            "NVIDIA transient error (batch=%d status=%s): %s",
                            batch_size, status, type(exc).__name__,
                        )
                        if status == 429:
                            await record_ops_event(
                                "nvidia_429", "retry", source="nvidia",
                                message=f"batch={batch_size}",
                            )
                    raise
    except RetryError as exc:
        logger.error("NVIDIA retries exhausted (batch=%d): %s", batch_size, exc.last_attempt.exception())
        await record_ops_event(
            "nvidia_exhausted", "error", source="nvidia",
            message=f"batch={batch_size}",
        )
        return None
    except Exception as exc:
        logger.error("NVIDIA call failed (batch=%d): %s", batch_size, exc)
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
    if not text.endswith("]"):
        last_brace = text.rfind("}")
        if last_brace > 0:
            text = text[: last_brace + 1] + "]"

    try:
        results = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("NVIDIA JSON parse failed: %s | raw[:200]=%s", exc, text[:200])
        return []

    output: list[tuple[Job, int, str]] = []
    for item in results:
        idx = item.get("job_index", 0)
        if idx >= len(jobs):
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

async def idle_rescore_for_user(
    user: User,
    session: AsyncSession,
) -> tuple[int, int, int]:
    """Two-phase rescore for a single user (Germany, ≤45 days).

    Returns (checked, upgraded, refreshed):
      checked   — priority (a): pre-filter rejects re-evaluated
      upgraded  — subset of `checked` that now has score > 0
      refreshed — priority (b): stale successful scores re-rated
    """
    profile = user.profile
    if not profile:
        return 0, 0, 0

    profile_text = build_profile_text(profile)
    budget = settings.nvidia_max_per_run
    batch_size = settings.max_jobs_per_scoring_batch
    country = settings.nvidia_country.lower()
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
            Job.country == country,
            Job.scraped_at >= age_cutoff,
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
                Job.country == country,
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
                refreshed += 1
            await session.commit()
            budget -= len(batch)

    if checked or refreshed:
        logger.info(
            "NVIDIA idle rescore [user %s]: checked=%d upgraded=%d refreshed=%d",
            user.telegram_id, checked, upgraded, refreshed,
        )

    return checked, upgraded, refreshed
