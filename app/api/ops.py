"""Ops dashboard — admin-only views over scan health and dedup metrics."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.api._helpers import get_role, get_user
from app.database import async_session
from app.models.job import Job
from app.services.ops_service import build_ops_overview
from app.services.scheduler_service import is_scan_running

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/ops/overview")
async def get_ops_overview(request: Request, window_hours: int = Query(24, ge=6, le=168)):
    if get_role(request, None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.scheduler_service import scheduler

    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return {"error": "No user"}

        bg_job = scheduler.get_job("background_scan")
        next_run = bg_job.next_run_time if bg_job else None
        return await build_ops_overview(
            session,
            user_id=user.id,
            window_hours=window_hours,
            next_run_at=next_run,
            scan_running=is_scan_running(),
        )


@router.get("/api/ops/dedup")
async def get_dedup_jobs(request: Request, limit: int = Query(200, ge=10, le=500)):
    """Return jobs that were fuzzy-merged from multiple sources."""
    if get_role(request, None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    async with async_session() as session:
        # raw_data is jsonb on prod; jsonb_array_length is the matching function.
        # Backed by GIN index ix_jobs_merged_sources on (raw_data->'merged_sources').
        result = await session.execute(
            select(Job)
            .where(
                Job.raw_data.op("->")("merged_sources").isnot(None),
                func.jsonb_array_length(Job.raw_data.op("->")("merged_sources")) > 1,
            )
            .order_by(Job.scraped_at.desc())
            .limit(limit)
        )
        jobs = result.scalars().all()
        return [
            {
                "id": j.id,
                "title": j.title,
                "company": j.company_name or "",
                "location": j.location or "",
                "url": j.url,
                "sources": (j.raw_data or {}).get("merged_sources", [j.source]),
                "posted_at": j.posted_at.isoformat() if j.posted_at else None,
            }
            for j in jobs
        ]
