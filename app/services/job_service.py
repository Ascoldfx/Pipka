from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import score_jobs
from app.scoring.rules import pre_filter
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams

logger = logging.getLogger(__name__)


async def search_and_score(
    aggregator: JobAggregator,
    params: SearchParams,
    user: User,
    session: AsyncSession,
    max_results: int = 100,
    skip_seen: bool = False,
) -> list[tuple[Job, int, str]]:
    """Search, filter by relevance bucket, score top candidates with AI."""
    all_jobs = await aggregator.search(params, session)

    # Get already-scored job IDs for this user (to prioritize new ones)
    already_scored_result = await session.execute(
        select(JobScore.job_id).where(JobScore.user_id == user.id)
    )
    already_scored_ids = {row[0] for row in already_scored_result.fetchall()}

    # Get hidden jobs (applied + rejected) — by ID and dedup_hash for robustness
    hidden_ids = await get_hidden_job_ids(user.id, session)
    hidden_hashes = await get_hidden_dedup_hashes(user.id, session)

    profile = user.profile
    new_high: list[Job] = []
    new_medium: list[Job] = []
    seen_high: list[Job] = []
    seen_medium: list[Job] = []

    for job in all_jobs:
        if job.id in hidden_ids or job.dedup_hash in hidden_hashes:
            continue
        passed, bucket = pre_filter(job, profile)
        if not passed:
            continue
        is_seen = job.id in already_scored_ids
        if bucket == "high":
            (seen_high if is_seen else new_high).append(job)
        elif bucket == "medium":
            (seen_medium if is_seen else new_medium).append(job)

    # Prioritize: new high → new medium → seen high → seen medium
    candidates = new_high + new_medium + seen_high + seen_medium

    logger.info(
        "Pre-filter: %d new_high, %d new_medium, %d seen_high, %d seen_medium (total: %d, hidden: %d)",
        len(new_high), len(new_medium), len(seen_high), len(seen_medium), len(candidates), len(hidden_ids),
    )

    if not candidates:
        return []

    # Score up to 80 candidates (more chances to find top matches)
    to_score = candidates[:80]
    scores = await score_jobs(to_score, user, session)

    # Build result tuples
    score_map = {s.job_id: s for s in scores}
    result: list[tuple[Job, int, str]] = []
    for job in to_score:
        s = score_map.get(job.id)
        if s:
            result.append((job, s.score, s.ai_analysis or ""))

    # Sort by score, only return jobs scoring 40+
    result.sort(key=lambda x: x[1], reverse=True)
    result = [r for r in result if r[1] >= 40]
    return result[:max_results]
