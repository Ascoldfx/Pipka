"""Dashboard API endpoints — serves HTML + JSON data for job browsing."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, case, desc, asc
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import analyze_single_job
from app.services.tracker_service import mark_applied, mark_rejected, save_job

router = APIRouter()

# For now, single-user — grab first active user
async def _get_user(session):
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.is_active == True).limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Serve the main dashboard HTML."""
    from pathlib import Path
    html_path = Path(__file__).parent.parent / "static" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@router.get("/api/jobs")
async def get_jobs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    sort: str = Query("score"),
    order: str = Query("desc"),
    min_score: int = Query(0, ge=0, le=100),
    source: str | None = Query(None),
    search: str | None = Query(None),
    status: str | None = Query(None),
):
    """Return paginated jobs with scores."""
    async with async_session() as session:
        user = await _get_user(session)
        if not user:
            return {"jobs": [], "total": 0, "page": page, "pages": 0}

        # Build base query: jobs LEFT JOIN scores for this user
        app_status = (
            select(Application.status)
            .where(Application.job_id == Job.id, Application.user_id == user.id)
            .correlate(Job)
            .scalar_subquery()
        )

        score_val = (
            select(JobScore.score)
            .where(JobScore.job_id == Job.id, JobScore.user_id == user.id)
            .correlate(Job)
            .scalar_subquery()
        )

        analysis_val = (
            select(JobScore.ai_analysis)
            .where(JobScore.job_id == Job.id, JobScore.user_id == user.id)
            .correlate(Job)
            .scalar_subquery()
        )

        stmt = select(
            Job,
            score_val.label("score"),
            analysis_val.label("analysis"),
            app_status.label("app_status"),
        )

        # Filters
        if min_score > 0:
            stmt = stmt.where(score_val >= min_score)

        if source:
            stmt = stmt.where(Job.source == source)

        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                Job.title.ilike(pattern) | Job.company_name.ilike(pattern)
            )

        if status == "new":
            stmt = stmt.where(app_status.is_(None))
        elif status:
            stmt = stmt.where(app_status == status)

        # Count
        from sqlalchemy import func as sqla_func
        count_stmt = select(sqla_func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        # Sort
        sort_col = {
            "score": score_val,
            "date": Job.posted_at,
            "salary": Job.salary_max,
            "title": Job.title,
            "company": Job.company_name,
        }.get(sort, score_val)

        if order == "asc":
            stmt = stmt.order_by(asc(sort_col).nulls_last())
        else:
            stmt = stmt.order_by(desc(sort_col).nulls_last())

        # Paginate
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        rows = result.all()

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
                "url": job.url,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency or "EUR",
                "is_remote": job.is_remote,
                "posted_at": job.posted_at.isoformat() if job.posted_at else None,
                "score": row[1],
                "analysis": row[2],
                "status": row[3],
            })

        pages = (total + per_page - 1) // per_page
        return {"jobs": jobs, "total": total, "page": page, "pages": pages}


@router.get("/api/stats")
async def get_stats():
    """Dashboard stats."""
    async with async_session() as session:
        user = await _get_user(session)
        if not user:
            return {}

        total_jobs = (await session.execute(select(func.count(Job.id)))).scalar() or 0

        scored = (await session.execute(
            select(func.count(JobScore.id)).where(JobScore.user_id == user.id)
        )).scalar() or 0

        top_count = (await session.execute(
            select(func.count(JobScore.id)).where(
                JobScore.user_id == user.id, JobScore.score >= 70
            )
        )).scalar() or 0

        applied = (await session.execute(
            select(func.count(Application.id)).where(
                Application.user_id == user.id, Application.status == "applied"
            )
        )).scalar() or 0

        sources = {}
        src_result = await session.execute(
            select(Job.source, func.count(Job.id)).group_by(Job.source)
        )
        for row in src_result:
            sources[row[0]] = row[1]

        return {
            "total_jobs": total_jobs,
            "scored": scored,
            "top_matches": top_count,
            "applied": applied,
            "sources": sources,
        }


@router.post("/api/jobs/{job_id}/action")
async def job_action(job_id: int, action: str = Query(...)):
    """Apply action to a job: save, applied, reject."""
    async with async_session() as session:
        user = await _get_user(session)
        if not user:
            return {"error": "No user"}

        if action == "save":
            await save_job(user.id, job_id, session)
        elif action == "applied":
            await mark_applied(user.id, job_id, session)
        elif action == "reject":
            await mark_rejected(user.id, job_id, session)
        else:
            return {"error": "Unknown action"}

        return {"ok": True, "action": action}


@router.get("/api/jobs/{job_id}/analyze")
async def analyze_job(job_id: int):
    """Run AI analysis for a single job."""
    async with async_session() as session:
        user = await _get_user(session)
        if not user or not user.profile:
            return {"error": "No user/profile"}

        job = await session.get(Job, job_id)
        if not job:
            return {"error": "Job not found"}

        analysis = await analyze_single_job(job, user.profile)
        return {"analysis": analysis}
