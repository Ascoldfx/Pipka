from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring.gemini_client import generate_gemini_content
from app.scoring.profile_hash import MODEL_CLAUDE, compute_profile_hash

logger = logging.getLogger(__name__)

SCORING_PROMPT = """\
You are a VERY strict Executive Recruiter AI. Score each job against the candidate profile REALISTICALLY.
Use BOTH the candidate's resume/background AND the explicitly listed target roles to assess fit.
The Target roles in the Candidate Profile are the source of truth. They may span several
functions (for example operations, transformation, restructuring, growth, or AI strategy).
Do not impose a fixed industry, function, or title hierarchy that is not present in the profile.

## Scoring Rules (CRITICAL — follow strictly, most jobs should score 30-60):
- 90-100: RARE. Near-exact target-role match, appropriate seniority, and exceptionally strong evidence from the resume/background
- 75-89: Strong match — same or closely related target function, appropriate scope/seniority, and relevant background
- 50-74: Partial match — adjacent role with meaningful overlap but clear gaps in scope, seniority, industry, or requirements
- 30-49: Weak — substantially different function or seniority, with only transferable-skill overlap
- 0-29: No match — completely wrong field, junior, or irrelevant

## Hard penalties (APPLY STRICTLY — these are MAXIMUM scores, not suggestions):
- Job is clearly outside every explicit target role and unsupported by the resume/background → max 40
- Job is in a clearly unrelated function (for example HR, Marketing, Sales, Finance, Legal, or Consulting) and is not an explicit target role → max 25
- Plain "Manager" role that is neither an explicit target nor comparable in scope to a target role → max 45
- Language requirements clearly conflict with an explicit profile preference or the candidate's documented proficiency → max 30
- Technical individual-contributor or hands-on engineering role unrelated to an explicit target role → max 35
- Junior/Trainee/Student → max 15
- Consulting/Advisory role outside the explicit target directions → max 35

## Key bonuses (only apply if base score is already decent):
- Direct or strong semantic match to an explicit target role → +10
- Scope and seniority match the candidate's demonstrated experience → +10
- Industry matches the candidate's documented background → +10
- Remote/hybrid option → +5
- Company context and requirements align with the candidate's specific experience → +5

## IMPORTANT:
- Ignore salary completely. It is absent from most listings and must not affect the score or verdict.
- Use the candidate's resume to assess relevant industry, past titles, scope, and years of experience.
- A target-role title is a preference, not proof of qualification: validate it against the resume.
- Judge abbreviations by their intended profile meaning and the vacancy context; do not silently reinterpret them as a different executive function.
- Be SKEPTICAL — most jobs score 40-65. Only genuinely strong target-role matches deserve 75+.

## Candidate Profile
{profile_text}

## Jobs to Score
{jobs_text}

## Instructions
For each job, return a JSON object with:
- "job_index": the index number
- "score": 0-100 (be strict — most jobs should score 30-60, only genuinely strong target-role matches get 70+)
- "breakdown": {{"relevance": 0-100, "seniority": 0-100, "language_fit": 0-100, "location": 0-100}}
- "verdict": 1-2 sentence assessment in Russian. Mention: seniority level, company type, language requirements, relevance to candidate's background.
- "red_flags": list of concerns (in Russian)

Return a JSON array. Only valid JSON, no markdown fences."""

client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global client
    if client is None:
        client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.claude_timeout_seconds,
            max_retries=settings.claude_max_retries,
        )
    return client


RESUME_MAX_CHARS = 2500  # keep prompt size sane; covers ~400 words of background


def build_profile_text(profile: UserProfile) -> str:
    parts: list[str] = []

    # --- Resume / background (most important context for AI matching) ---
    if profile.resume_text:
        resume = profile.resume_text.strip()
        if len(resume) > RESUME_MAX_CHARS:
            resume = resume[:RESUME_MAX_CHARS] + "\n[resume truncated]"
        parts.append(f"### Candidate Resume / Background\n{resume}")

    # --- Preferences ---
    # Salary / experience / language preferences are intentionally absent:
    # incomplete listing data made them prompt noise rather than useful signals.
    prefs: list[str] = []
    if profile.target_titles:
        prefs.append(f"Target roles: {', '.join(profile.target_titles)}")
    if profile.work_mode:
        prefs.append(f"Work mode: {profile.work_mode}")
    if profile.preferred_countries:
        prefs.append(f"Countries: {', '.join(profile.preferred_countries)}")
    if prefs:
        parts.append("### Preferences\n" + "\n".join(prefs))

    # --- Hard exclusions ---
    if profile.excluded_keywords:
        parts.append(
            "### CRITICAL EXCLUSIONS\n"
            "Score < 20 for any job requiring these: "
            + ", ".join(profile.excluded_keywords)
        )
    if getattr(profile, "english_only", False):
        parts.append(
            "### Language requirement\n"
            "Candidate wants ENGLISH-ONLY jobs. "
            "Jobs entirely in German/French/Dutch → max 30. "
            "International/English-language companies → strong bonus."
        )

    return "\n\n".join(parts) or "No profile set"


async def score_jobs(
    jobs: list[Job], user: User, session: AsyncSession
) -> list[JobScore]:
    profile = user.profile
    if not profile:
        return []

    # Check cache — single batch SELECT instead of N+1 queries.
    # Phase 2b: cache hit requires profile_hash match (or legacy NULL).
    cache_cutoff = datetime.now() - timedelta(hours=settings.score_cache_hours)
    job_ids = [j.id for j in jobs]
    current_hash = compute_profile_hash(profile)
    cached_result = await session.execute(
        select(JobScore).where(
            JobScore.job_id.in_(job_ids),
            JobScore.user_id == user.id,
            JobScore.scored_at > cache_cutoff,
            (JobScore.profile_hash.is_(None)) | (JobScore.profile_hash == current_hash),
        )
    )
    cached_map = {s.job_id: s for s in cached_result.scalars().all()}
    cached_ids: set[int] = set(cached_map.keys())
    cached_scores: list[JobScore] = list(cached_map.values())

    to_score = [j for j in jobs if j.id not in cached_ids]
    if not to_score:
        return cached_scores

    # Batch score
    profile_text = build_profile_text(profile)
    profile_hash = compute_profile_hash(profile)
    model_version = MODEL_CLAUDE()
    new_scores: list[JobScore] = []

    for i in range(0, len(to_score), settings.max_jobs_per_scoring_batch):
        batch = to_score[i : i + settings.max_jobs_per_scoring_batch]
        batch_scores = await _score_batch(
            batch, profile_text, user.id, session,
            profile_hash=profile_hash, model_version=model_version,
        )
        new_scores.extend(batch_scores)

    all_scores = cached_scores + new_scores
    all_scores.sort(key=lambda s: s.score, reverse=True)
    return all_scores


async def _score_batch(
    jobs: list[Job],
    profile_text: str,
    user_id: int,
    session: AsyncSession,
    *,
    profile_hash: str | None = None,
    model_version: str | None = None,
) -> list[JobScore]:
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

    prompt = SCORING_PROMPT.format(profile_text=profile_text, jobs_text=jobs_text)

    import asyncio
    
    ai = _get_client()
    max_retries = 3
    text = None
    for attempt in range(max_retries):
        try:
            response = await ai.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_scoring_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning("Claude scoring attempt %d failed: %s. Retrying in %ds...", attempt + 1, e, wait_time)
                await asyncio.sleep(wait_time)
            else:
                logger.error("Claude scoring failed after %d attempts: %s", max_retries, e)
                return []
                
    try:
        # Strip markdown fences if present
        if "```" in text:
            text = text.split("```json")[-1] if "```json" in text else text.split("```")[-2] if text.count("```") >= 2 else text
            text = text.replace("```", "").strip()
        # Try to fix truncated JSON
        text = text.strip()
        if not text.endswith("]"):
            # Find last complete object
            last_brace = text.rfind("}")
            if last_brace > 0:
                text = text[:last_brace + 1] + "]"
        results = json.loads(text)
    except Exception as e:
        logger.error("Claude parsing JSON failed: %s. Output was: %s", e, text)
        return []

    # Phase 2b: bulk UPSERT instead of per-row flush+IntegrityError.
    # ON CONFLICT DO UPDATE overwrites stale rows whose profile_hash differs;
    # the WHERE clause leaves matching ones untouched (no churn) and legacy
    # NULL ones too (NULL != X is unknown, not true).
    rows = []
    for item in results:
        idx = item.get("job_index", 0)
        if idx >= len(jobs):
            continue
        job = jobs[idx]
        rows.append({
            "job_id": job.id,
            "user_id": user_id,
            "score": min(100, max(0, int(item.get("score", 0)))),
            "ai_analysis": item.get("verdict", ""),
            "breakdown": item.get("breakdown"),
            "profile_hash": profile_hash,
            "model_version": model_version,
        })

    if not rows:
        return []

    stmt = pg_insert(JobScore).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["job_id", "user_id"],
        set_={
            "score": stmt.excluded.score,
            "ai_analysis": stmt.excluded.ai_analysis,
            "breakdown": stmt.excluded.breakdown,
            "scored_at": datetime.now(),
            "profile_hash": stmt.excluded.profile_hash,
            "model_version": stmt.excluded.model_version,
        },
        where=JobScore.profile_hash != stmt.excluded.profile_hash,
    ).returning(JobScore.id, JobScore.job_id)

    try:
        result = await session.execute(stmt)
        inserted_job_ids = {row.job_id for row in result.all()}
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("_score_batch UPSERT failed for user_id=%s", user_id)
        return []

    # Build returned ORM-tracked instances mirroring the caller's contract.
    by_job: dict[int, dict] = {r["job_id"]: r for r in rows}
    scores: list[JobScore] = []
    for jid in inserted_job_ids:
        r = by_job[jid]
        scores.append(JobScore(
            job_id=r["job_id"],
            user_id=r["user_id"],
            score=r["score"],
            ai_analysis=r["ai_analysis"],
            breakdown=r["breakdown"],
            profile_hash=r["profile_hash"],
            model_version=r["model_version"],
        ))
    return scores


async def analyze_single_job(job: Job, profile: UserProfile) -> str:
    """Detailed analysis of a single job for the inline button."""
    profile_text = build_profile_text(profile)
    prompt = (
        f"Ты Executive Recruiter. Профиль кандидата:\n{profile_text}\n\n"
        f"Вакансия: {job.title}\nКомпания: {job.company_name}\n"
        f"Локация: {job.location} ({job.country})\n"
        f"Описание: {(job.description or '')[:1500]}\n\n"
        "Дай детальный анализ: совпадение, плюсы, минусы, рекомендации. "
        "Если вакансия на немецком, переведи суть на русский. Ответ на русском."
    )
    
    if settings.gemini_api_key:
        try:
            response = await generate_gemini_content(
                prompt,
                model=settings.gemini_analysis_model,
                max_output_tokens=settings.gemini_analysis_max_output_tokens,
            )
            return response.text
        except Exception as e:
            logger.error("Gemini analysis error: %s", e)
            return f"Ошибка анализа Gemini: {str(e)[:100]}"
            
    try:
        ai = _get_client()
        response = await ai.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_analysis_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"Ошибка анализа: {str(e)[:100]}"
