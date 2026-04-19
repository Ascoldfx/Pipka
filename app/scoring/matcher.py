from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile

logger = logging.getLogger(__name__)

SCORING_PROMPT = """\
You are a VERY strict Executive Recruiter AI. Score each job against the candidate profile REALISTICALLY.
Use BOTH the candidate's resume background AND the target role preferences to assess fit.
The candidate is looking EXCLUSIVELY for Director / Head of / VP / C-level positions in SUPPLY CHAIN, PROCUREMENT, OPERATIONS, or LOGISTICS at INTERNATIONAL companies with ENGLISH as working language.

## Scoring Rules (CRITICAL — follow strictly, most jobs should score 30-60):
- 90-100: RARE. Perfect match — Director+ level, Supply Chain/Procurement/Operations title, well-known international company, English-speaking, candidate's background is a strong fit
- 75-89: Strong match — Director+ level, clearly related domain (supply chain/procurement/operations/logistics), English OK, background relevant
- 50-74: Partial match — related but gaps (Senior Manager level, slightly different function, language concerns, unknown company)
- 30-49: Weak — different function (IT, HR, Finance, Marketing, Sales, Consulting), or German-only, or plain Manager
- 0-29: No match — completely wrong field, junior, or irrelevant

## Hard penalties (APPLY STRICTLY — these are MAXIMUM scores, not suggestions):
- Job is NOT in Supply Chain/Procurement/Operations/Logistics → max 40
- Job is in HR/Marketing/Sales/Finance/IT/Consulting/Legal → max 25
- Plain "Manager" title (not Director/Head/VP/Chief/Lead) → max 45
- Job requires fluent German (C1+/native/"verhandlungssicher"/"fließend"/"sehr gute Deutschkenntnisse") → max 30 (candidate has B1!)
- Description entirely in German with no English mentioned → max 35
- Job requires TECHNICAL/IT/ENGINEERING skills → max 35
- Local German SME (Mittelstand) with no international presence → max 45
- Junior/Trainee/Student → max 15
- Consulting/Advisory role → max 35

## Key bonuses (only apply if base score is already decent):
- International/English-speaking company → +10
- "English" as working language → +5
- Industry matches candidate's background (FMCG, manufacturing, food & beverage, retail) → +10
- Remote/hybrid option → +5
- Company/industry aligns with candidate's specific experience → +5

## IMPORTANT:
- Salary not shown → do NOT penalize, note "зарплата не указана"
- If salary IS shown and seems below expectation → mention in verdict but do NOT hard-cap the score (salary data is unreliable)
- Use the candidate's resume to assess fit: relevant industry, past titles, years of experience
- Be SKEPTICAL — most jobs score 40-65. Only truly matching Director+ international SC/Procurement roles deserve 75+

## Candidate Profile
{profile_text}

## Jobs to Score
{jobs_text}

## Instructions
For each job, return a JSON object with:
- "job_index": the index number
- "score": 0-100 (be strict — most jobs should score 30-60, only truly matching Director+ international roles get 70+)
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
    prefs: list[str] = []
    if profile.target_titles:
        prefs.append(f"Target roles: {', '.join(profile.target_titles)}")
    if profile.experience_years:
        prefs.append(f"Experience: {profile.experience_years}+ years")
    if profile.languages:
        lang_str = ", ".join(f"{k.upper()}: {v}" for k, v in profile.languages.items())
        prefs.append(f"Languages: {lang_str}")
    if profile.work_mode:
        prefs.append(f"Work mode: {profile.work_mode}")
    if profile.preferred_countries:
        prefs.append(f"Countries: {', '.join(profile.preferred_countries)}")
    if profile.industries:
        prefs.append(f"Industries: {', '.join(profile.industries)}")
    if profile.min_salary:
        prefs.append(f"Target salary: {profile.min_salary}+ EUR (note: many listings omit salary — do not penalise if absent)")
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

    # Check cache — single batch SELECT instead of N+1 queries
    cache_cutoff = datetime.now() - timedelta(hours=settings.score_cache_hours)
    job_ids = [j.id for j in jobs]
    cached_result = await session.execute(
        select(JobScore).where(
            JobScore.job_id.in_(job_ids),
            JobScore.user_id == user.id,
            JobScore.scored_at > cache_cutoff,
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
    new_scores: list[JobScore] = []

    for i in range(0, len(to_score), settings.max_jobs_per_scoring_batch):
        batch = to_score[i : i + settings.max_jobs_per_scoring_batch]
        batch_scores = await _score_batch(batch, profile_text, user.id, session)
        new_scores.extend(batch_scores)

    all_scores = cached_scores + new_scores
    all_scores.sort(key=lambda s: s.score, reverse=True)
    return all_scores


async def _score_batch(
    jobs: list[Job], profile_text: str, user_id: int, session: AsyncSession
) -> list[JobScore]:
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

    prompt = SCORING_PROMPT.format(profile_text=profile_text, jobs_text=jobs_text)

    try:
        ai = _get_client()
        response = await ai.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_scoring_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
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
        logger.error("Claude scoring failed: %s", e)
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
            await session.flush()  # catch IntegrityError early, per-row
            scores.append(score_obj)
        except IntegrityError:
            # Race condition: another task (backfill/scan) already inserted this score
            await session.rollback()
            logger.debug("Score for job_id=%s user_id=%s already exists (race), skipping", job.id, user_id)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning("_score_batch commit IntegrityError for user_id=%s, partial batch discarded", user_id)
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
