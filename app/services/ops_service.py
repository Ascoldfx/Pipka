from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.ops_event import OpsEvent
from app.models.user import User, UserProfile

logger = logging.getLogger(__name__)


async def record_ops_event(
    event_type: str,
    status: str,
    *,
    source: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist an operational event without breaking the main user flow."""
    try:
        async with async_session() as session:
            session.add(
                OpsEvent(
                    event_type=event_type,
                    status=status,
                    source=source,
                    message=message,
                    payload=payload,
                )
            )
            await session.commit()
    except Exception as exc:  # pragma: no cover - observability must fail open
        logger.warning("Could not record ops event %s/%s: %s", event_type, status, exc)


async def build_ops_overview(
    session,
    *,
    user_id: int,
    window_hours: int,
    next_run_at: datetime | None,
    scan_running: bool,
) -> dict[str, Any]:
    now = datetime.now()
    window_start = now - timedelta(hours=window_hours)
    recent_week = now - timedelta(days=7)

    total_jobs = (await session.execute(select(func.count(Job.id)))).scalar() or 0
    jobs_recent = (
        await session.execute(select(func.count(Job.id)).where(Job.scraped_at >= window_start))
    ).scalar() or 0
    jobs_week = (
        await session.execute(select(func.count(Job.id)).where(Job.scraped_at >= recent_week))
    ).scalar() or 0

    scored_total = (
        await session.execute(select(func.count(JobScore.id)).where(JobScore.user_id == user_id))
    ).scalar() or 0
    scored_recent = (
        await session.execute(
            select(func.count(JobScore.id)).where(
                JobScore.user_id == user_id,
                JobScore.scored_at >= window_start,
            )
        )
    ).scalar() or 0
    top_matches = (
        await session.execute(
            select(func.count(JobScore.id)).where(
                JobScore.user_id == user_id,
                JobScore.score >= 70,
            )
        )
    ).scalar() or 0
    top_recent = (
        await session.execute(
            select(func.count(JobScore.id)).where(
                JobScore.user_id == user_id,
                JobScore.score >= 70,
                JobScore.scored_at >= window_start,
            )
        )
    ).scalar() or 0
    avg_score_recent = (
        await session.execute(
            select(func.avg(JobScore.score)).where(
                JobScore.user_id == user_id,
                JobScore.scored_at >= window_start,
            )
        )
    ).scalar()

    score_alias = aliased(JobScore)
    unscored_total = (
        await session.execute(
            select(func.count(Job.id))
            .select_from(Job)
            .outerjoin(
                score_alias,
                (score_alias.job_id == Job.id) & (score_alias.user_id == user_id),
            )
            .where(score_alias.id.is_(None))
        )
    ).scalar() or 0

    coverage_pct = round((scored_total / total_jobs) * 100, 1) if total_jobs else 0.0
    pending_pct = round((unscored_total / total_jobs) * 100, 1) if total_jobs else 0.0

    source_rows = await session.execute(
        select(Job.source, func.count(Job.id))
        .group_by(Job.source)
        .order_by(func.count(Job.id).desc(), Job.source.asc())
    )
    total_by_source = {row[0]: row[1] for row in source_rows}

    recent_source_rows = await session.execute(
        select(Job.source, func.count(Job.id))
        .where(Job.scraped_at >= window_start)
        .group_by(Job.source)
        .order_by(func.count(Job.id).desc(), Job.source.asc())
    )
    recent_by_source = {row[0]: row[1] for row in recent_source_rows}

    application_rows = await session.execute(
        select(Application.status, func.count(Application.id))
        .where(Application.user_id == user_id)
        .group_by(Application.status)
    )
    status_counts = {row[0]: row[1] for row in application_rows}

    action_rows = await session.execute(
        select(Application.status, func.count(Application.id))
        .where(
            Application.user_id == user_id,
            Application.updated_at >= window_start,
        )
        .group_by(Application.status)
    )
    actions_recent = {row[0]: row[1] for row in action_rows}

    scan_rows = await session.execute(
        select(OpsEvent)
        .where(OpsEvent.event_type == "scan")
        .order_by(OpsEvent.created_at.desc())
        .limit(8)
    )
    recent_scans = list(scan_rows.scalars())
    last_scan = recent_scans[0] if recent_scans else None

    # Jooble API budget: sum api_requests from ALL scan events to track total usage
    all_scan_rows = await session.execute(
        select(OpsEvent)
        .where(OpsEvent.event_type == "scan", OpsEvent.status == "success")
    )
    jooble_requests_total = 0
    for ev in all_scan_rows.scalars():
        if not ev.payload:
            continue
        for src in (ev.payload.get("aggregator") or {}).get("sources", []):
            if src.get("source") == "jooble":
                jooble_requests_total += src.get("api_requests", 0)
    jooble_budget = 500  # default free tier

    api_401 = (
        await session.execute(
            select(func.count(OpsEvent.id)).where(
                OpsEvent.event_type == "api_error",
                OpsEvent.created_at >= window_start,
                OpsEvent.message.like("%-> 401%"),
            )
        )
    ).scalar() or 0
    api_500 = (
        await session.execute(
            select(func.count(OpsEvent.id)).where(
                OpsEvent.event_type == "api_error",
                OpsEvent.created_at >= window_start,
                OpsEvent.message.like("%-> 5%"),
            )
        )
    ).scalar() or 0

    event_rows = await session.execute(
        select(OpsEvent).order_by(OpsEvent.created_at.desc()).limit(12)
    )
    recent_events = list(event_rows.scalars())

    sources = []
    for source, total in total_by_source.items():
        recent = recent_by_source.get(source, 0)
        fresh_share = round((recent / jobs_recent) * 100, 1) if jobs_recent else 0.0
        total_share = round((total / total_jobs) * 100, 1) if total_jobs else 0.0
        sources.append(
            {
                "name": source,
                "total": total,
                "recent": recent,
                "fresh_share": fresh_share,
                "total_share": total_share,
            }
        )

    # ── Users / clients ──────────────────────────────────────────
    total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (
        await session.execute(select(func.count(User.id)).where(User.is_active == True))
    ).scalar() or 0
    users_with_profile = (
        await session.execute(select(func.count(UserProfile.id)))
    ).scalar() or 0
    users_telegram = (
        await session.execute(
            select(func.count(User.id)).where(User.telegram_id.is_not(None))
        )
    ).scalar() or 0
    users_google = (
        await session.execute(
            select(func.count(User.id)).where(User.google_sub.is_not(None))
        )
    ).scalar() or 0
    new_users_window = (
        await session.execute(
            select(func.count(User.id)).where(User.created_at >= window_start)
        )
    ).scalar() or 0

    # Per-user activity: scores + actions in window
    user_rows = await session.execute(
        select(User).where(User.is_active == True).order_by(User.created_at.desc())
    )
    all_users = list(user_rows.scalars())

    user_activity = []
    for u in all_users:
        scores_total = (
            await session.execute(
                select(func.count(JobScore.id)).where(JobScore.user_id == u.id)
            )
        ).scalar() or 0
        scores_window = (
            await session.execute(
                select(func.count(JobScore.id)).where(
                    JobScore.user_id == u.id,
                    JobScore.scored_at >= window_start,
                )
            )
        ).scalar() or 0
        actions_total = (
            await session.execute(
                select(func.count(Application.id)).where(Application.user_id == u.id)
            )
        ).scalar() or 0
        actions_window = (
            await session.execute(
                select(func.count(Application.id)).where(
                    Application.user_id == u.id,
                    Application.updated_at >= window_start,
                )
            )
        ).scalar() or 0
        last_score_at = (
            await session.execute(
                select(func.max(JobScore.scored_at)).where(JobScore.user_id == u.id)
            )
        ).scalar()
        user_activity.append({
            "id": u.id,
            "name": u.name or "—",
            "role": u.role,
            "auth": ("telegram" if u.telegram_id else "") + ("+" if u.telegram_id and u.google_sub else "") + ("google" if u.google_sub else ""),
            "tier": u.subscription_tier,
            "has_profile": scores_total > 0 or actions_total > 0,
            "scores_total": scores_total,
            "scores_window": scores_window,
            "actions_total": actions_total,
            "actions_window": actions_window,
            "last_active": last_score_at.isoformat() + "Z" if last_score_at else None,
            "joined": u.created_at.isoformat() + "Z" if u.created_at else None,
        })

    return {
        "window_hours": window_hours,
        "kpis": {
            "total_jobs": total_jobs,
            "jobs_recent": jobs_recent,
            "jobs_week": jobs_week,
            "scored_total": scored_total,
            "scored_recent": scored_recent,
            "unscored_total": unscored_total,
            "coverage_pct": coverage_pct,
            "pending_pct": pending_pct,
            "top_matches": top_matches,
            "top_recent": top_recent,
            "avg_score_recent": round(float(avg_score_recent), 1) if avg_score_recent is not None else None,
            "active_sources": len(total_by_source),
            "api_401": api_401,
            "api_500": api_500,
        },
        "pipeline": {
            "collected": jobs_recent,
            "scored": scored_recent,
            "top_matches": top_recent,
            "applied": actions_recent.get("applied", 0),
            "rejected": actions_recent.get("rejected", 0),
            "saved": actions_recent.get("saved", 0),
        },
        "applications": {
            "total": status_counts,
            "recent": actions_recent,
        },
        "sources": sources,
        "users": {
            "total": total_users,
            "active": active_users,
            "with_profile": users_with_profile,
            "via_telegram": users_telegram,
            "via_google": users_google,
            "new_in_window": new_users_window,
            "activity": user_activity,
        },
        "jooble": {
            "requests_total": jooble_requests_total,
            "budget": jooble_budget,
            "remaining": max(0, jooble_budget - jooble_requests_total),
            "pct_used": round(jooble_requests_total / jooble_budget * 100, 1) if jooble_budget else 0,
        },
        "scan": {
            "running": scan_running,
            "next_run": next_run_at.isoformat() if next_run_at else None,
            "last_status": last_scan.status if last_scan else "unknown",
            "last_at": last_scan.created_at.isoformat() + "Z" if last_scan else None,
            "last_message": last_scan.message if last_scan else None,
            "last_payload": last_scan.payload if last_scan else None,
            "recent": [
                {
                    "status": event.status,
                    "at": event.created_at.isoformat() + "Z",
                    "message": event.message,
                    "payload": event.payload,
                }
                for event in recent_scans
            ],
        },
        "events": [
            {
                "type": event.event_type,
                "status": event.status,
                "source": event.source,
                "message": event.message,
                "at": event.created_at.isoformat() + "Z",
                "payload": event.payload,
            }
            for event in recent_events
        ],
    }
