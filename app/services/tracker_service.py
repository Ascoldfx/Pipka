from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application import Application, ApplicationHistory
from app.models.job import Job, JobScore
from app.models.user import UserProfile

logger = logging.getLogger(__name__)

VALID_STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")

AUTO_EXCLUDE_THRESHOLD = 5  # rejections from one company → auto-add to excluded_keywords


async def save_job(user_id: int, job_id: int, session: AsyncSession) -> Application:
    existing = await session.execute(
        select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
    )
    app = existing.scalar_one_or_none()
    if app:
        return app

    app = Application(user_id=user_id, job_id=job_id, status="saved")
    session.add(app)
    await session.commit()
    return app


async def mark_applied(user_id: int, job_id: int, session: AsyncSession) -> Application:
    existing = await session.execute(
        select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
    )
    app = existing.scalar_one_or_none()
    if app:
        old_status = app.status
        app.status = "applied"
        app.applied_at = datetime.now()
        history = ApplicationHistory(application_id=app.id, old_status=old_status, new_status="applied")
        session.add(history)
    else:
        app = Application(user_id=user_id, job_id=job_id, status="applied", applied_at=datetime.now())
        session.add(app)
        await session.flush()
        history = ApplicationHistory(application_id=app.id, old_status=None, new_status="applied")
        session.add(history)

    await session.commit()
    return app


async def get_applied_job_ids(user_id: int, session: AsyncSession) -> set[int]:
    """Get all job IDs where the user has applied — these should be hidden from search."""
    result = await session.execute(
        select(Application.job_id).where(
            Application.user_id == user_id,
            Application.status == "applied",
        )
    )
    return {row[0] for row in result.fetchall()}


async def get_unreviewed_jobs(user_id: int, session: AsyncSession, min_score: int = 0) -> list[tuple[Job, int, str]]:
    """Get scored jobs that the user hasn't reacted to (no Application with applied/rejected status)."""
    # Find all JobScore for the user
    stmt = (
        select(Job, JobScore.score, JobScore.ai_analysis)
        .join(JobScore, Job.id == JobScore.job_id)
        .outerjoin(Application, (Application.job_id == Job.id) & (Application.user_id == user_id))
        .where(
            JobScore.user_id == user_id,
            JobScore.score >= min_score,
            (Application.id == None) | (~Application.status.in_(["applied", "rejected", "withdrawn"]))
        )
        .order_by(JobScore.score.desc())
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def mark_rejected(user_id: int, job_id: int, session: AsyncSession) -> Application:
    existing = await session.execute(
        select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
    )
    app = existing.scalar_one_or_none()
    if app:
        old_status = app.status
        app.status = "rejected"
        history = ApplicationHistory(application_id=app.id, old_status=old_status, new_status="rejected")
        session.add(history)
    else:
        app = Application(user_id=user_id, job_id=job_id, status="rejected")
        session.add(app)
        await session.flush()
        history = ApplicationHistory(application_id=app.id, old_status=None, new_status="rejected")
        session.add(history)

    await session.commit()
    return app


async def get_hidden_job_ids(user_id: int, session: AsyncSession) -> set[int]:
    """Get job IDs that should be hidden: applied + rejected."""
    result = await session.execute(
        select(Application.job_id).where(
            Application.user_id == user_id,
            Application.status.in_(["applied", "rejected"]),
        )
    )
    return {row[0] for row in result.fetchall()}


async def get_hidden_dedup_hashes(user_id: int, session: AsyncSession) -> set[str]:
    """Get dedup hashes of applied + rejected jobs — survives DB resets."""
    result = await session.execute(
        select(Job.dedup_hash)
        .join(Application, Application.job_id == Job.id)
        .where(
            Application.user_id == user_id,
            Application.status.in_(["applied", "rejected"]),
        )
    )
    return {row[0] for row in result.fetchall()}


async def update_status(app_id: int, new_status: str, note: str | None, session: AsyncSession) -> Application | None:
    if new_status not in VALID_STATUSES:
        return None

    result = await session.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        return None

    old_status = app.status
    app.status = new_status
    if new_status == "applied" and not app.applied_at:
        app.applied_at = datetime.now()

    history = ApplicationHistory(application_id=app.id, old_status=old_status, new_status=new_status, note=note)
    session.add(history)
    await session.commit()
    return app


async def get_user_applications(user_id: int, session: AsyncSession, status: str | None = None) -> list[Application]:
    stmt = select(Application).options(selectinload(Application.job)).where(Application.user_id == user_id)
    if status:
        stmt = stmt.where(Application.status == status)
    stmt = stmt.order_by(Application.updated_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_pipeline_stats(user_id: int, session: AsyncSession) -> dict[str, int]:
    apps = await get_user_applications(user_id, session)
    stats: dict[str, int] = {}
    for app in apps:
        stats[app.status] = stats.get(app.status, 0) + 1
    return stats


async def check_auto_exclude_company(
    user_id: int, job_id: int, session: AsyncSession
) -> str | None:
    """After a rejection: if this company has been rejected >= AUTO_EXCLUDE_THRESHOLD times,
    auto-add the company name to excluded_keywords and zero-out unactioned scores for all
    remaining jobs from that company.

    Returns the company name string if auto-exclusion was triggered, else None.
    Must be called *after* mark_rejected() has committed.
    """
    job = await session.get(Job, job_id)
    if not job or not job.company_name:
        return None

    company = job.company_name.strip()
    if len(company) < 3:
        return None

    # Count total rejections for this company (case-insensitive, all jobs from it)
    count_result = await session.execute(
        select(func.count(Application.id))
        .join(Job, Application.job_id == Job.id)
        .where(
            Application.user_id == user_id,
            Application.status == "rejected",
            func.lower(Job.company_name) == company.lower(),
        )
    )
    rejection_count = count_result.scalar() or 0
    if rejection_count < AUTO_EXCLUDE_THRESHOLD:
        return None

    # Load profile
    profile_result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return None

    # Already in excluded list?
    current = list(profile.excluded_keywords or [])
    if any(kw.lower() == company.lower() for kw in current):
        return None  # nothing new to do

    # Add to excluded_keywords
    current.append(company)
    profile.excluded_keywords = current
    session.add(profile)

    # Collect all job IDs from this company
    company_job_ids_result = await session.execute(
        select(Job.id).where(func.lower(Job.company_name) == company.lower())
    )
    company_job_ids = [row[0] for row in company_job_ids_result.fetchall()]

    zeroed = 0
    if company_job_ids:
        # Don't touch jobs the user has already actioned (applied / rejected)
        actioned_result = await session.execute(
            select(Application.job_id).where(
                Application.user_id == user_id,
                Application.job_id.in_(company_job_ids),
                Application.status.in_(["applied", "rejected"]),
            )
        )
        actioned_ids = {row[0] for row in actioned_result.fetchall()}
        to_zero = [jid for jid in company_job_ids if jid not in actioned_ids]

        if to_zero:
            result = await session.execute(
                sa_update(JobScore)
                .where(
                    JobScore.user_id == user_id,
                    JobScore.job_id.in_(to_zero),
                )
                .values(score=0, ai_analysis="✗ auto-excluded (company blocked)")
            )
            zeroed = result.rowcount

    await session.commit()
    logger.info(
        "Auto-excluded company %r for user_id=%s (%d rejections); zeroed %d scores",
        company, user_id, rejection_count, zeroed,
    )
    return company
