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
    max_results: int = 30,
) -> list[tuple[Job, int, str]]:
    """Search, filter, score. Returns list of (job, score, verdict)."""
    all_jobs = await aggregator.search(params, session)

    # Pre-filter with rules
    profile = user.profile
    candidates: list[Job] = []
    for job in all_jobs:
        passed, bucket = pre_filter(job, profile)
        if passed:
            candidates.append(job)
        if len(candidates) >= max_results * 2:
            break

    if not candidates:
        return []

    # AI scoring (top N)
    to_score = candidates[:max_results]
    scores = await score_jobs(to_score, user, session)

    # Build result tuples
    score_map = {s.job_id: s for s in scores}
    result: list[tuple[Job, int, str]] = []
    for job in to_score:
        s = score_map.get(job.id)
        if s:
            result.append((job, s.score, s.ai_analysis or ""))
        else:
            result.append((job, 0, ""))

    result.sort(key=lambda x: x[1], reverse=True)
    return result[:max_results]
