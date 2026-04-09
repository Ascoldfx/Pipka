from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions
from app.database import async_session
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import score_jobs
from app.scoring.rules import pre_filter
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids
from app.sources.adzuna import AdzunaSource
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources.jobspy_source import JobSpySource

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# All search queries for background scanning
SCAN_QUERIES = [
    "Director Supply Chain",
    "Head of Procurement",
    "VP Supply Chain",
    "Director Operations",
    "Head of Logistics",
    "Chief Operating Officer",
    "VP Procurement",
    "Director Purchasing",
    "Head of Sourcing",
    "Global Supply Chain Director",
    "Director Supply Chain English",
    "Head of Procurement international",
    "VP Operations international",
    "Director Global Sourcing",
    "Chief Procurement Officer",
]

TOP_SCORE_THRESHOLD = 80  # Push to Telegram if score >= this


def start_scheduler(bot_app):
    """Start background job scanner."""
    # Run every 3 hours
    scheduler.add_job(
        _background_scan,
        "interval",
        hours=3,
        args=[bot_app],
        id="background_scan",
        replace_existing=True,
    )
    # Also run 2 minutes after startup
    scheduler.add_job(
        _background_scan,
        "date",
        run_date=datetime.now().replace(second=0, microsecond=0),
        args=[bot_app],
        id="startup_scan",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Background scanner started (every 3 hours)")


async def _background_scan(bot_app):
    """Scan all sources, score only NEW jobs, push top results to Telegram."""
    logger.info("Background scan started")

    aggregator = JobAggregator([AdzunaSource(), JobSpySource()])

    params = SearchParams(
        queries=SCAN_QUERIES,
        countries=["de", "at", "nl", "ch", "be"],
        locations=[],
    )

    async with async_session() as session:
        # 1. Collect and store jobs (aggregator handles dedup + upsert)
        all_jobs = await aggregator.search(params, session)
        logger.info("Background scan: %d jobs in DB after aggregation", len(all_jobs))

        # 2. Find all users with profiles
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        for user in users:
            if not user.profile:
                continue

            try:
                await _score_and_notify(bot_app, user, all_jobs, session)
            except Exception as e:
                logger.error("Background scan failed for user %s: %s", user.telegram_id, e)

    logger.info("Background scan completed")


async def _score_and_notify(bot_app, user: User, all_jobs: list[Job], session):
    """Score new jobs for user, push top ones to Telegram."""
    # Get already-scored job IDs
    scored_result = await session.execute(
        select(JobScore.job_id).where(JobScore.user_id == user.id)
    )
    already_scored_ids = {row[0] for row in scored_result.fetchall()}

    # Get hidden (applied + rejected)
    hidden_ids = await get_hidden_job_ids(user.id, session)
    hidden_hashes = await get_hidden_dedup_hashes(user.id, session)

    # Filter to only NEW, unhidden jobs
    new_jobs = []
    for job in all_jobs:
        if job.id in already_scored_ids:
            continue
        if job.id in hidden_ids or job.dedup_hash in hidden_hashes:
            continue
        passed, bucket = pre_filter(job, user.profile)
        if passed and bucket in ("high", "medium"):
            new_jobs.append(job)

    if not new_jobs:
        logger.info("No new jobs to score for user %s", user.telegram_id)
        return

    logger.info("Scoring %d new jobs for user %s", len(new_jobs), user.telegram_id)

    # Score only new jobs (max 80 per run to control costs)
    to_score = new_jobs[:80]
    scores = await score_jobs(to_score, user, session)

    # Find top results to push
    top_results = []
    for s in scores:
        if s.score >= TOP_SCORE_THRESHOLD:
            job = next((j for j in to_score if j.id == s.job_id), None)
            if job:
                top_results.append((job, s))

    if not top_results:
        logger.info("No top results for user %s (scored %d)", user.telegram_id, len(scores))
        return

    # Sort by score desc
    top_results.sort(key=lambda x: x[1].score, reverse=True)

    # Push to Telegram
    count = len(top_results)
    header = f"🔥 Найдено {count} {'новая топ-вакансия' if count == 1 else 'новых топ-вакансий'}!\n\nАвтоматический скан — только лучшие совпадения (score {TOP_SCORE_THRESHOLD}+)"
    await bot_app.bot.send_message(chat_id=user.telegram_id, text=header)

    for job, score_obj in top_results[:10]:  # Max 10 push notifications
        card = format_job_card(job, score=score_obj.score)
        if score_obj.ai_analysis:
            card += f"\n\n💬 {score_obj.ai_analysis}"
        await bot_app.bot.send_message(
            chat_id=user.telegram_id,
            text=card,
            reply_markup=job_actions(job.id),
        )

    logger.info("Pushed %d top jobs to user %s", min(count, 10), user.telegram_id)
