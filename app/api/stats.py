"""Per-user dashboard stats (with TTL cache) and the public homepage stats."""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api._helpers import get_user
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User

router = APIRouter()

# ─── Per-user stats cache (30s TTL) ───────────────────────────
# Tuple value: (expires_at_monotonic, payload).
_STATS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_STATS_TTL_SECONDS = 30.0


def invalidate_stats_cache(user_id: Optional[int] = None) -> None:
    """Drop the cached stats payload for a single user (after they take an
    action) or all users (admin force-refresh). Called from jobs.py."""
    if user_id is None:
        _STATS_CACHE.clear()
    else:
        _STATS_CACHE.pop(user_id, None)


@router.get("/api/stats")
async def get_stats(request: Request):
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return {}

        cached = _STATS_CACHE.get(user.id)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > time.monotonic():
                return payload

        total_jobs = (await session.execute(select(func.count(Job.id)))).scalar() or 0
        scored = (await session.execute(
            select(func.count(JobScore.id)).where(JobScore.user_id == user.id)
        )).scalar() or 0
        top_count = (await session.execute(
            select(func.count(JobScore.id)).where(JobScore.user_id == user.id, JobScore.score >= 70)
        )).scalar() or 0
        applied = (await session.execute(
            select(func.count(Application.id)).where(
                Application.user_id == user.id, Application.status == "applied"
            )
        )).scalar() or 0
        rejected = (await session.execute(
            select(func.count(Application.id)).where(
                Application.user_id == user.id, Application.status == "rejected"
            )
        )).scalar() or 0

        inbox_stmt = select(func.count(JobScore.id)).outerjoin(
            Application, (Application.job_id == JobScore.job_id) & (Application.user_id == user.id)
        ).where(
            JobScore.user_id == user.id,
            (Application.id == None) | (~Application.status.in_(["applied", "rejected"]))  # noqa: E711
        )
        inbox_count = (await session.execute(inbox_stmt)).scalar() or 0

        sources: dict[str, int] = {}
        src_result = await session.execute(select(Job.source, func.count(Job.id)).group_by(Job.source))
        for row in src_result:
            sources[row[0]] = row[1]

        payload = {
            "total_jobs": total_jobs, "scored": scored, "top_matches": top_count,
            "applied": applied, "rejected": rejected, "inbox": inbox_count, "sources": sources,
        }
        _STATS_CACHE[user.id] = (time.monotonic() + _STATS_TTL_SECONDS, payload)
        return payload


# ─── Public landing-page stats (5-min TTL, no auth) ───────────
_PUBLIC_STATS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PUBLIC_STATS_TTL = 300.0


@router.get("/api/public/stats")
async def get_public_stats():
    now = time.monotonic()
    cached = _PUBLIC_STATS_CACHE.get("stats")
    if cached is not None:
        expires_at, payload = cached
        if expires_at > now:
            return payload

    async with async_session() as session:
        total_jobs = (await session.execute(select(func.count(Job.id)))).scalar() or 0
        total_analyses = (await session.execute(select(func.count(JobScore.id)))).scalar() or 0
        prefilter_rejected = (await session.execute(
            select(func.count(JobScore.id)).where(
                JobScore.score == 0, JobScore.ai_analysis.is_(None),
            )
        )).scalar() or 0
        top_matches = (await session.execute(
            select(func.count(JobScore.id)).where(JobScore.score >= 70)
        )).scalar() or 0
        sources_count = (await session.execute(
            select(func.count(func.distinct(Job.source)))
        )).scalar() or 0
        active_users = (await session.execute(
            select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
        )).scalar() or 0

        payload = {
            "total_jobs_collected": total_jobs,
            "ai_analyses_performed": total_analyses,
            "prefilter_rejected": prefilter_rejected,
            "top_matches": top_matches,
            "active_sources": sources_count,
            "active_users": active_users,
        }
        _PUBLIC_STATS_CACHE["stats"] = (now + _PUBLIC_STATS_TTL, payload)
        return payload
