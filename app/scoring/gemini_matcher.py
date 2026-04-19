"""Gemini Flash scorer — free-tier alternative to Claude for backfill scoring.

Free tier limits (as of 2026):
  gemini-2.0-flash-lite: 30 RPM, 1 500 RPD, 1M TPM
  gemini-2.0-flash:      15 RPM, 1 500 RPD, 1M TPM

Used exclusively for _backfill_score() — non-urgent, runs every 2 h.
Real-time scoring (scan → _score_and_notify) still uses Claude.

Activation: set GEMINI_API_KEY in .env.
Leave it empty to fall back to Claude for backfill too.
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import SCORING_PROMPT, build_profile_text

logger = logging.getLogger(__name__)

_gemini_model = None  # lazy singleton


def _get_model():
    global _gemini_model
    if _gemini_model is None:
        import google.generativeai as genai  # noqa: PLC0415

        genai.configure(api_key=settings.gemini_api_key)
        _gemini_model = genai.GenerativeModel(settings.gemini_model)
        logger.info("Gemini model initialised: %s", settings.gemini_model)
    return _gemini_model


async def score_jobs_gemini(
    jobs: list[Job],
    user: User,
    session: AsyncSession,
) -> list[JobScore]:
    """Score a list of jobs using Gemini Flash.

    Identical contract to ``score_jobs()`` in matcher.py so the caller
    can swap them transparently.
    """
    profile = user.profile
    if not profile:
        return []

    profile_text = build_profile_text(profile)
    new_scores: list[JobScore] = []

    for i in range(0, len(jobs), settings.max_jobs_per_scoring_batch):
        # Respect rate limits — sleep between every batch except the first
        if i > 0:
            await asyncio.sleep(settings.gemini_batch_delay)

        batch = jobs[i : i + settings.max_jobs_per_scoring_batch]
        batch_scores = await _score_batch(batch, profile_text, user.id, session)
        new_scores.extend(batch_scores)

    return new_scores


async def _score_batch(
    jobs: list[Job],
    profile_text: str,
    user_id: int,
    session: AsyncSession,
) -> list[JobScore]:
    jobs_text = ""
    for idx, job in enumerate(jobs):
        desc_preview = (job.description or "")[:1200]
        salary_info = ""
        if job.salary_min or job.salary_max:
            salary_info = f"Salary: {job.salary_min or '?'}-{job.salary_max or '?'} {job.salary_currency or 'EUR'}"
        remote_info = (
            f"Remote: {'Yes' if job.is_remote else 'No' if job.is_remote is False else 'Unknown'}"
        )
        jobs_text += (
            f"\n### Job {idx}\n"
            f"Title: {job.title}\n"
            f"Company: {job.company_name or 'N/A'}\n"
            f"Location: {job.location or 'N/A'} ({job.country or 'N/A'})\n"
            f"{salary_info}\n{remote_info}\n"
            f"Description: {desc_preview}\n"
        )

    prompt = SCORING_PROMPT.format(profile_text=profile_text, jobs_text=jobs_text)

    try:
        model = _get_model()
        # generate_content_async is available in google-generativeai >= 0.4
        response = await model.generate_content_async(prompt)
        text = response.text.strip()

        # Strip markdown fences if the model wrapped the JSON
        if "```" in text:
            if "```json" in text:
                text = text.split("```json", 1)[-1]
            elif text.count("```") >= 2:
                text = text.split("```")[1]
            text = text.replace("```", "").strip()

        # Fix truncated JSON (network/token limit edge case)
        if not text.endswith("]"):
            last_brace = text.rfind("}")
            if last_brace > 0:
                text = text[: last_brace + 1] + "]"

        results = json.loads(text)
    except Exception as exc:
        logger.error("Gemini scoring failed (user_id=%s, batch_size=%d): %s", user_id, len(jobs), exc)
        return []

    scores: list[JobScore] = []
    for item in results:
        idx = item.get("job_index", 0)
        if idx >= len(jobs):
            continue
        job = jobs[idx]
        score_obj = JobScore(
            job_id=job.id,
            user_id=user_id,
            score=min(100, max(0, int(item.get("score", 0)))),
            ai_analysis=item.get("verdict", ""),
            breakdown=item.get("breakdown"),
        )
        try:
            session.add(score_obj)
            await session.flush()  # catch integrity errors per-row
            scores.append(score_obj)
        except IntegrityError:
            await session.rollback()
            logger.debug(
                "Gemini: score for job_id=%s user_id=%s already exists (race), skipping",
                job.id,
                user_id,
            )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning(
            "Gemini _score_batch commit IntegrityError for user_id=%s, partial batch discarded",
            user_id,
        )

    return scores
