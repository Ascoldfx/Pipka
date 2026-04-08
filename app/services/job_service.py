from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.user import User
from app.scoring.matcher import score_jobs
from app.scoring.rules import pre_filter
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams


async def search_and_score(
    aggregator: JobAggregator,
    params: SearchParams,
    user: User,
    session: AsyncSession,
    max_results: int = 15,
) -> list[tuple[Job, int, str]]:
    """Search, filter by relevance bucket, score top candidates with AI."""
    all_jobs = await aggregator.search(params, session)

    profile = user.profile
    high: list[Job] = []
    medium: list[Job] = []

    for job in all_jobs:
        passed, bucket = pre_filter(job, profile)
        if not passed:
            continue
        if bucket == "high":
            high.append(job)
        elif bucket == "medium":
            medium.append(job)

    # Prioritize high-bucket, then medium
    candidates = high + medium

    if not candidates:
        return []

    # Score more candidates to find the best ones (up to 50)
    to_score = candidates[:50]
    scores = await score_jobs(to_score, user, session)

    # Build result tuples
    score_map = {s.job_id: s for s in scores}
    result: list[tuple[Job, int, str]] = []
    for job in to_score:
        s = score_map.get(job.id)
        if s:
            result.append((job, s.score, s.ai_analysis or ""))

    # Sort by score, only return jobs scoring 50+
    result.sort(key=lambda x: x[1], reverse=True)
    result = [r for r in result if r[1] >= 50]
    return result[:max_results]
