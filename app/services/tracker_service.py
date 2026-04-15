from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application import Application, ApplicationHistory
from app.models.job import Job

VALID_STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")


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
