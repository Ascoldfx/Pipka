"""Jobs endpoints — listing, country breakdown, per-job actions, AI analysis."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_, asc, desc, func, select

from app.api._helpers import VALID_ACTIONS, get_user
from app.api._ratelimit import check_rate_limit
from app.api.stats import invalidate_stats_cache
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.scoring.matcher import analyze_single_job
from app.services.tracker_service import (
    check_auto_exclude_company,
    mark_applied,
    mark_rejected,
    save_job,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/countries")
async def get_countries(request: Request):
    """Return distinct countries present in the jobs table with job counts."""
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return []
        result = await session.execute(
            select(Job.country, func.count(Job.id).label("cnt"))
            .where(Job.country.is_not(None), Job.country != "")
            .group_by(Job.country)
            .order_by(func.count(Job.id).desc())
        )
        return [{"code": row[0].lower(), "count": row[1]} for row in result.all()]


@router.get("/api/jobs")
async def get_jobs(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    sort: str = Query("score"),
    order: str = Query("desc"),
    min_score: int = Query(0, ge=0, le=100),
    source: str | None = Query(None),
    search: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
    country: str | None = Query(None),
    countries: str | None = Query(None),  # comma-separated country codes, e.g. "de,pl,cz"
):
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return {"jobs": [], "total": 0, "page": page, "pages": 0}

        score_join = and_(JobScore.job_id == Job.id, JobScore.user_id == user.id)
        app_join = and_(Application.job_id == Job.id, Application.user_id == user.id)

        filters = []
        if min_score > 0:
            filters.append(JobScore.score >= min_score)
        if source:
            filters.append(Job.source == source)
        if search:
            term = search.strip()
            if term:
                # Escape LIKE special chars so they're treated literally
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                filters.append(
                    Job.title.ilike(pattern, escape="\\") | Job.company_name.ilike(pattern, escape="\\")
                )
        if status == "new":
            filters.append(Application.status.is_(None))
        elif status:
            filters.append(Application.status == status)

        if region == "saxony":
            filters.append(
                Job.location.ilike("%Leipzig%") | Job.location.ilike("%Dresden%") |
                Job.location.ilike("%Halle%") | Job.location.ilike("%Chemnitz%") |
                Job.location.ilike("%Sachsen%") | Job.location.ilike("%Saxony%")
            )
        elif region == "germany":
            filters.append(Job.country == "de")
        elif region == "dach":
            filters.append(Job.country.in_(["de", "at", "ch"]))
        elif region == "europe":
            filters.append(Job.country.in_(["de", "at", "ch", "nl", "be", "lu", "dk", "pl", "cz", "si", "sk", "ro", "hu"]))
        elif region == "cee":
            filters.append(Job.country.in_(["si", "sk", "ro", "hu"]))

        # Multi-country filter takes precedence over legacy single country
        if countries:
            codes = [c.strip().lower() for c in countries.split(",") if c.strip()]
            if codes:
                filters.append(func.lower(Job.country).in_(codes))
        elif country:
            filters.append(Job.country.ilike(country))

        count_stmt = (
            select(func.count(Job.id))
            .select_from(Job)
            .outerjoin(JobScore, score_join)
            .outerjoin(Application, app_join)
        )
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = (await session.execute(count_stmt)).scalar() or 0

        sort_col = {
            "score": JobScore.score,
            "date": Job.posted_at,
            "salary": Job.salary_max,
            "title": Job.title,
            "company": Job.company_name,
        }.get(sort, JobScore.score)

        order_clause = asc(sort_col).nulls_last() if order == "asc" else desc(sort_col).nulls_last()

        stmt = (
            select(
                Job,
                JobScore.score.label("score"),
                JobScore.ai_analysis.label("analysis"),
                Application.status.label("app_status"),
            )
            .select_from(Job)
            .outerjoin(JobScore, score_join)
            .outerjoin(Application, app_join)
        )
        if filters:
            stmt = stmt.where(*filters)
        stmt = stmt.order_by(order_clause).offset((page - 1) * per_page).limit(per_page)

        rows = (await session.execute(stmt)).all()

        jobs = []
        for row in rows:
            job = row[0]
            jobs.append({
                "id": job.id,
                "title": job.title,
                "company": job.company_name or "N/A",
                "location": job.location or "N/A",
                "country": job.country or "?",
                "source": job.source,
                "merged_sources": (job.raw_data or {}).get("merged_sources"),
                "url": job.url,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency or "EUR",
                "is_remote": job.is_remote,
                "posted_at": job.posted_at.isoformat() if job.posted_at else None,
                "score": row[1],
                "analysis": row[2],
                "status": row[3],
                "data_quality": "full" if len(job.description or "") >= 300 else "partial",
            })

        pages = (total + per_page - 1) // per_page
        return {"jobs": jobs, "total": total, "page": page, "pages": pages}


@router.post("/api/jobs/{job_id}/action")
async def job_action(job_id: int, request: Request, action: str = Query(...)):
    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    async with async_session() as session:
        try:
            user = await get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")
            auto_excluded: str | None = None
            if action == "save":
                await save_job(user.id, job_id, session)
            elif action == "applied":
                await mark_applied(user.id, job_id, session)
            elif action == "reject":
                await mark_rejected(user.id, job_id, session)
                auto_excluded = await check_auto_exclude_company(user.id, job_id, session)
            invalidate_stats_cache(user.id)
            return {"ok": True, "action": action, "auto_excluded": auto_excluded}
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("job_action failed: job_id=%s action=%s", job_id, action)
            raise HTTPException(status_code=500, detail="Action failed")


@router.get("/api/jobs/{job_id}/analyze")
async def analyze_job(job_id: int, request: Request):
    async with async_session() as session:
        user = await get_user(request, session)
        if not user or not user.profile:
            raise HTTPException(status_code=404, detail="No user/profile")
        # Each call burns one Gemini/Claude request — without a cap a logged-in
        # user could exhaust the daily AI quota with a click-spam loop.
        check_rate_limit(user_id=user.id, key="analyze", limit=30, window_s=3600)
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            analysis = await analyze_single_job(job, user.profile)
        except Exception:
            logger.exception("analyze_single_job failed: job_id=%s", job_id)
            raise HTTPException(status_code=502, detail="Analysis service unavailable")
        return {"analysis": analysis}
