"""Dashboard API endpoints — serves HTML + JSON data for job browsing.

Auth model (v2): Google OAuth session-based only. /api/me is handled by auth.py.
All data endpoints require a valid session cookie (pipka_session).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring.matcher import analyze_single_job
from app.services.tracker_service import mark_applied, mark_rejected, save_job

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_ACTIONS = {"save", "applied", "reject"}
VALID_WORK_MODES = {"remote", "hybrid", "onsite", "any"}

# ─── Stats cache ──────────────────────────────────────────────
_STATS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_STATS_TTL_SECONDS = 30.0


def _invalidate_stats_cache(user_id: Optional[int] = None) -> None:
    if user_id is None:
        _STATS_CACHE.clear()
    else:
        _STATS_CACHE.pop(user_id, None)


# ─── Auth helpers ─────────────────────────────────────────────

async def _get_session_user(request: Request, session) -> User | None:
    """Get user from session cookie (Google OAuth)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id, User.is_active == True)
    )
    return result.scalar_one_or_none()


async def _get_user(request: Request, session) -> User | None:
    """Get current user from session cookie (Google OAuth). Returns None if not authenticated."""
    return await _get_session_user(request, session)


def _get_role(request: Request, user: User | None) -> str:
    """Determine user role from session or legacy Basic Auth."""
    # Session-based role (Google OAuth)
    session_role = request.session.get("user_role")
    if session_role:
        return session_role
    # User model role
    if user and user.role:
        return user.role
    return "guest"


def _require_authenticated(request: Request):
    """Raise 401 if no user session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")


def _require_admin(request: Request):
    """Raise 403 if user is not admin."""
    role = request.session.get("user_role", "guest")
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


# ─── Pages ────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard_page():
    from pathlib import Path
    html_path = Path(__file__).parent.parent / "static" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ─── Jobs ─────────────────────────────────────────────────────

@router.get("/api/countries")
async def get_countries(request: Request):
    """Return distinct countries present in the jobs table with job counts."""
    async with async_session() as session:
        user = await _get_user(request, session)
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
        user = await _get_user(request, session)
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
            # Trim whitespace, escape LIKE special chars (% and _) to treat them literally
            term = search.strip()
            if term:
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
async def get_stats(request: Request):
    async with async_session() as session:
        user = await _get_user(request, session)
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
            (Application.id == None) | (~Application.status.in_(["applied", "rejected"]))
        )
        inbox_count = (await session.execute(inbox_stmt)).scalar() or 0

        sources = {}
        src_result = await session.execute(select(Job.source, func.count(Job.id)).group_by(Job.source))
        for row in src_result:
            sources[row[0]] = row[1]

        payload = {
            "total_jobs": total_jobs, "scored": scored, "top_matches": top_count,
            "applied": applied, "rejected": rejected, "inbox": inbox_count, "sources": sources,
        }
        _STATS_CACHE[user.id] = (time.monotonic() + _STATS_TTL_SECONDS, payload)
        return payload


# ─── Actions (require login) ─────────────────────────────────

@router.post("/api/jobs/{job_id}/action")
async def job_action(job_id: int, request: Request, action: str = Query(...)):
    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    async with async_session() as session:
        try:
            user = await _get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")
            if action == "save":
                await save_job(user.id, job_id, session)
            elif action == "applied":
                await mark_applied(user.id, job_id, session)
            elif action == "reject":
                await mark_rejected(user.id, job_id, session)
            _invalidate_stats_cache(user.id)
            return {"ok": True, "action": action}
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("job_action failed: job_id=%s action=%s", job_id, action)
            raise HTTPException(status_code=500, detail="Action failed")


@router.get("/api/jobs/{job_id}/analyze")
async def analyze_job(job_id: int, request: Request):
    async with async_session() as session:
        user = await _get_user(request, session)
        if not user or not user.profile:
            raise HTTPException(status_code=404, detail="No user/profile")
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            analysis = await analyze_single_job(job, user.profile)
        except Exception:
            logger.exception("analyze_single_job failed: job_id=%s", job_id)
            raise HTTPException(status_code=502, detail="Analysis service unavailable")
        return {"analysis": analysis}


# ─── Manual Scan (admin only) ────────────────────────────────

_manual_scan_lock = asyncio.Lock()


@router.post("/api/scan")
async def trigger_scan(request: Request):
    """Trigger a background job scan manually."""
    role = _get_role(request, None)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.scheduler_service import _background_scan, scheduler

    if _manual_scan_lock.locked():
        return {"status": "already_running"}

    bg_job = scheduler.get_job("background_scan")
    if not bg_job:
        return {"error": "Scheduler not initialized"}

    bot_app = bg_job.args[0]

    async def _run():
        async with _manual_scan_lock:
            try:
                await _background_scan(bot_app)
            except Exception as e:
                logger.error("Manual scan failed: %s", e)

    task = asyncio.create_task(_run())
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "started"}


@router.get("/api/scan/status")
async def scan_status():
    from app.services.scheduler_service import scheduler
    bg_job = scheduler.get_job("background_scan")
    if not bg_job:
        return {"next_run": None}
    next_run = bg_job.next_run_time
    return {"next_run": next_run.isoformat() if next_run else None}


# ─── Profile / Settings ──────────────────────────────────────

@router.get("/api/profile")
async def get_profile(request: Request):
    async with async_session() as session:
        user = await _get_user(request, session)
        if not user:
            return {"error": "No user"}
        p = user.profile
        if not p:
            return {"profile": None}
        return {"profile": {
            "resume_text": p.resume_text or "",
            "target_titles": p.target_titles or [],
            "min_salary": p.min_salary,
            "max_commute_km": p.max_commute_km,
            "languages": p.languages or {},
            "experience_years": p.experience_years,
            "industries": p.industries or [],
            "work_mode": p.work_mode or "any",
            "preferred_countries": p.preferred_countries or [],
            "excluded_keywords": p.excluded_keywords or [],
            "english_only": getattr(p, "english_only", False) or False,
        }}


MAX_RESUME_CHARS = 100_000


@router.post("/api/profile")
async def update_profile(
    request: Request,
    resume_text: str = Form(None),
    target_titles: str = Form(None),
    min_salary: int = Form(None),
    languages: str = Form(None),
    experience_years: int = Form(None),
    industries: str = Form(None),
    work_mode: str = Form(None),
    preferred_countries: str = Form(None),
    excluded_keywords: str = Form(None),
    english_only: str = Form(None),
):
    if resume_text is not None and len(resume_text) > MAX_RESUME_CHARS:
        raise HTTPException(status_code=400, detail=f"Resume too long (>{MAX_RESUME_CHARS} chars)")
    if min_salary is not None and not (0 <= min_salary <= 1_000_000):
        raise HTTPException(status_code=400, detail="min_salary out of range")
    if experience_years is not None and not (0 <= experience_years <= 80):
        raise HTTPException(status_code=400, detail="experience_years out of range")
    if work_mode is not None and work_mode not in VALID_WORK_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid work_mode. Must be one of: {', '.join(VALID_WORK_MODES)}")

    async with async_session() as session:
        try:
            user = await _get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")

            p = user.profile
            if not p:
                p = UserProfile(user_id=user.id)
                session.add(p)

            if resume_text is not None:
                p.resume_text = resume_text
            if target_titles is not None:
                p.target_titles = [t.strip() for t in target_titles.split(",") if t.strip()]
            if min_salary is not None:
                p.min_salary = min_salary
            if languages is not None:
                try:
                    p.languages = json.loads(languages)
                except json.JSONDecodeError:
                    langs = {}
                    for part in languages.split(","):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            langs[k.strip()] = v.strip()
                    p.languages = langs
            if experience_years is not None:
                p.experience_years = experience_years
            if industries is not None:
                p.industries = [i.strip() for i in industries.split(",") if i.strip()]
            if work_mode is not None:
                p.work_mode = work_mode
            if preferred_countries is not None:
                p.preferred_countries = [c.strip().lower() for c in preferred_countries.split(",") if c.strip()]
            if excluded_keywords is not None:
                p.excluded_keywords = [k.strip() for k in excluded_keywords.split(",") if k.strip()]
            if english_only is not None:
                p.english_only = english_only in ("1", "true", "True", "yes", "on")

            await session.commit()
            _invalidate_stats_cache(user.id)
            return {"ok": True}
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("update_profile failed")
            raise HTTPException(status_code=500, detail="Profile update failed")


MAX_RESUME_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/api/profile/resume")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    """Upload resume file and extract text (PDF, DOCX, TXT)."""
    content = await file.read()
    if len(content) > MAX_RESUME_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (>10MB)")

    filename = (file.filename or "").lower()
    text = ""

    # Validate magic bytes to prevent extension spoofing
    _MAGIC = {
        ".pdf": b"%PDF",
        ".docx": b"PK\x03\x04",
    }
    if filename.endswith((".pdf", ".docx")):
        ext = ".pdf" if filename.endswith(".pdf") else ".docx"
        if not content[:4].startswith(_MAGIC[ext]):
            raise HTTPException(status_code=400, detail="File content does not match declared format")

    if filename.endswith(".pdf"):
        try:
            import io
            from pdfminer.high_level import extract_text as pdf_extract
            text = pdf_extract(io.BytesIO(content))
        except Exception:
            logger.exception("PDF parse failed: filename=%s", filename)
            raise HTTPException(status_code=400, detail="Could not parse PDF")

    elif filename.endswith(".docx"):
        try:
            import io
            import zipfile
            import xml.etree.ElementTree as ET
            zf = zipfile.ZipFile(io.BytesIO(content))
            xml_content = zf.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                if texts:
                    paragraphs.append("".join(texts))
            text = "\n".join(paragraphs)
        except Exception:
            logger.exception("DOCX parse failed: filename=%s", filename)
            raise HTTPException(status_code=400, detail="Could not parse DOCX")

    elif filename.endswith(".txt"):
        text = content.decode("utf-8", errors="ignore")

    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use PDF, DOCX, or TXT.")

    text = text.replace("\x00", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Could not extract text from file")
    if len(text) > MAX_RESUME_CHARS:
        text = text[:MAX_RESUME_CHARS]

    async with async_session() as session:
        try:
            user = await _get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")
            p = user.profile
            if not p:
                p = UserProfile(user_id=user.id)
                session.add(p)
            p.resume_text = text
            await session.commit()
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("upload_resume DB save failed")
            raise HTTPException(status_code=500, detail="Failed to save resume")

    return {"ok": True, "length": len(text), "preview": text[:500]}
