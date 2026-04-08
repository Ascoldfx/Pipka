from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile

logger = logging.getLogger(__name__)

SCORING_PROMPT = """\
You are a strict Executive Recruiter AI. Score each job against the candidate profile REALISTICALLY.

## Scoring Rules (CRITICAL — follow strictly):
- 90-100: Perfect match — same role, same industry, right seniority, right location, language OK
- 70-89: Strong match — closely related role, transferable skills, minor gaps (e.g. language)
- 50-69: Partial match — related domain but different function, or significant gaps
- 30-49: Weak match — only tangential relevance, major gaps
- 0-29: No match — completely different field or role

## Key penalties (APPLY STRICTLY):
- If the job requires TECHNICAL/IT/ENGINEERING skills the candidate doesn't have → max 50
- If the job title contains "Junior", "Trainee", "Werkstudent" → max 20
- If the job requires fluent German (C1+/C2/native/"verhandlungssicher"/"fließend") → max 30 (candidate has only B1!)
- If the job description is entirely in German with no English mentioned → subtract 20 points (likely requires strong German)
- If the job is in a completely different domain (e.g. IT development, medical, legal) → max 40
- If location doesn't match and no remote → subtract 10-15 points
- If the job REQUIRES specific industry experience the candidate DOES NOT HAVE (e.g. pharma/biotech GMP, automotive IATF, aerospace DO-178, banking regulations) → max 55. The candidate has FMCG/tea manufacturing experience ONLY.
- If the job requires specialized tools/systems the candidate doesn't know (beyond SAP MM/SD) → subtract 10-15 points
- READ THE FULL DESCRIPTION carefully. Do not score based on title alone. Look for hard requirements like certifications, specific industry experience, specialized tools.

## Key bonuses:
- If the job is at an international/English-speaking company → +10 points
- If description mentions "English" as working language → +10 points
- If the job is at a startup, international company, or explicitly says "English-speaking environment" → +10 points

## IMPORTANT — salary filter:
- If candidate has min_salary set and the job shows a salary BELOW that threshold → max 40
- If no salary is shown, do NOT penalize — but mention "зарплата не указана" in verdict

## Candidate Profile
{profile_text}

## Jobs to Score
{jobs_text}

## Instructions
For each job, return a JSON object with:
- "job_index": the index number
- "score": 0-100 (realistic, use the full range — most jobs should be 30-70)
- "breakdown": {{"relevance": 0-100, "seniority": 0-100, "language_fit": 0-100, "location": 0-100}}
- "verdict": 1-2 sentence assessment in Russian. Be honest and specific.
- "red_flags": list of concerns (in Russian)

Return a JSON array. Only valid JSON, no markdown fences."""

client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global client
    if client is None:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return client


def build_profile_text(profile: UserProfile) -> str:
    parts = []
    if profile.resume_text:
        parts.append(profile.resume_text)
    if profile.target_titles:
        parts.append(f"Target roles: {', '.join(profile.target_titles)}")
    if profile.experience_years:
        parts.append(f"Experience: {profile.experience_years}+ years")
    if profile.languages:
        lang_str = ", ".join(f"{k.upper()}: {v}" for k, v in profile.languages.items())
        parts.append(f"Languages: {lang_str}")
    if profile.base_location:
        parts.append(f"Location: {profile.base_location}")
    if profile.work_mode:
        parts.append(f"Work mode: {profile.work_mode}")
    if profile.preferred_countries:
        parts.append(f"Countries: {', '.join(profile.preferred_countries)}")
    if profile.industries:
        parts.append(f"Industries: {', '.join(profile.industries)}")
    if profile.min_salary:
        parts.append(f"Min salary: {profile.min_salary} EUR")
    return "\n".join(parts) or "No profile set"


async def score_jobs(
    jobs: list[Job], user: User, session: AsyncSession
) -> list[JobScore]:
    profile = user.profile
    if not profile:
        return []

    # Check cache
    cache_cutoff = datetime.now() - timedelta(hours=settings.score_cache_hours)
    cached_ids: set[int] = set()
    cached_scores: list[JobScore] = []

    for job in jobs:
        result = await session.execute(
            select(JobScore).where(
                JobScore.job_id == job.id,
                JobScore.user_id == user.id,
                JobScore.scored_at > cache_cutoff,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            cached_ids.add(job.id)
            cached_scores.append(existing)

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
            model="claude-sonnet-4-20250514",
            max_tokens=5000,
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
        session.add(score_obj)
        scores.append(score_obj)

    await session.commit()
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
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        return f"Ошибка анализа: {str(e)[:100]}"
