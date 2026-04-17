from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions
from app.config import settings
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.matcher import score_jobs
from app.scoring.rules import pre_filter
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources import AdzunaSource, JobSpySource, ArbeitnowSource, RemotiveSource, ArbeitsagenturSource

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
    """Start background job scanner and cleanup tasks."""
    # Run every 3 hours
    scheduler.add_job(
        _background_scan,
        "interval",
        hours=3,
        args=[bot_app],
        id="background_scan",
        replace_existing=True,
    )
    # Run 30 seconds after startup (give time for everything to init)
    scheduler.add_job(
        _background_scan,
        "date",
        run_date=datetime.now() + timedelta(seconds=30),
        args=[bot_app],
        id="startup_scan",
        replace_existing=True,
    )
    # Daily cleanup at 03:00 UTC — delete jobs older than job_max_age_days
    scheduler.add_job(
        _cleanup_old_jobs,
        "cron",
        hour=3,
        minute=0,
        id="daily_cleanup",
        replace_existing=True,
    )
    # Backfill scorer: every 6 hours — score existing unscored jobs for each user
    scheduler.add_job(
        _backfill_score,
        "interval",
        hours=6,
        id="backfill_score",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Background scanner started (every 3 hours, first scan in 30s)")


async def _background_scan(bot_app):
    """Scan all sources, score only NEW jobs, push top results to Telegram."""
    logger.info("Background scan started")

    aggregator = JobAggregator([AdzunaSource(), JobSpySource(), ArbeitnowSource(), RemotiveSource(), ArbeitsagenturSource()])

    async with async_session() as session:
        # 1. Find all users with profiles to determine dynamic search scope
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        dynamic_queries = set()
        dynamic_countries = set()
        
        for user in users:
            if user.profile:
                if user.profile.target_titles:
                    dynamic_queries.update(user.profile.target_titles)
                if user.profile.preferred_countries:
                    dynamic_countries.update(user.profile.preferred_countries)
                    
        # Fallbacks to defaults if nothing found in profiles
        final_queries = list(dynamic_queries) if dynamic_queries else SCAN_QUERIES
        final_countries = list(dynamic_countries) if dynamic_countries else ["de", "at", "nl", "ch", "be", "si", "sk", "ro", "hu"]

        params = SearchParams(
            queries=final_queries,
            countries=final_countries,
            locations=[],
        )

        # 2. Collect and store jobs (aggregator handles dedup + upsert)
        all_jobs = await aggregator.search(params, session)
        logger.info("Background scan: %d jobs in DB after aggregation (Params: %s / %s)", len(all_jobs), final_queries, final_countries)

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


async def _backfill_score():
    """Score existing DB jobs that haven't been scored yet for each user.

    Runs every 6 hours. Picks up jobs that were added during scans but skipped
    due to the per-run 80-job cap, or jobs loaded before a user created their profile.
    """
    logger.info("Backfill scorer started")

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        for user in users:
            if not user.profile:
                continue
            try:
                # All jobs in DB (newest first, limit to recent window)
                cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)
                all_jobs_result = await session.execute(
                    select(Job).where(Job.scraped_at >= cutoff)
                )
                all_jobs = all_jobs_result.scalars().all()

                # Already scored for this user
                scored_result = await session.execute(
                    select(JobScore.job_id).where(JobScore.user_id == user.id)
                )
                already_scored_ids = {row[0] for row in scored_result.fetchall()}

                hidden_ids = await get_hidden_job_ids(user.id, session)
                hidden_hashes = await get_hidden_dedup_hashes(user.id, session)

                unscored = []
                for job in all_jobs:
                    if job.id in already_scored_ids:
                        continue
                    if job.id in hidden_ids or job.dedup_hash in hidden_hashes:
                        continue
                    passed, bucket = pre_filter(job, user.profile)
                    if passed and bucket in ("high", "medium"):
                        unscored.append(job)

                if not unscored:
                    continue

                # Score up to 120 per run (3 batches of 8 × 5 batches)
                to_score = unscored[:120]
                logger.info("Backfill: scoring %d unscored jobs for user %s", len(to_score), user.telegram_id)
                await score_jobs(to_score, user, session)

            except Exception as e:
                logger.error("Backfill scorer failed for user %s: %s", user.telegram_id, e)

    logger.info("Backfill scorer completed")


async def _cleanup_old_jobs():
    """Delete jobs older than job_max_age_days that have no applied/saved applications.

    Logic:
    - Jobs with applied/interviewing/offer status are KEPT forever (user cares about them)
    - Jobs with only rejected/saved or no application are deleted after max_age_days
    - Cascade: JobScore rows deleted automatically via FK
    """
    cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)
    logger.info("Running daily cleanup: deleting jobs scraped before %s", cutoff.date())

    async with async_session() as session:
        # Find job IDs that have an "active" application (applied/interviewing/offer)
        active_app_result = await session.execute(
            select(Application.job_id).where(
                Application.status.in_(["applied", "interviewing", "offer"])
            )
        )
        protected_ids = {row[0] for row in active_app_result.fetchall()}

        # Find old jobs NOT in protected list
        old_jobs_result = await session.execute(
            select(Job.id).where(Job.scraped_at < cutoff)
        )
        old_job_ids = [row[0] for row in old_jobs_result.fetchall() if row[0] not in protected_ids]

        if not old_job_ids:
            logger.info("Cleanup: nothing to delete")
            return

        # Delete in chunks to avoid huge IN clauses
        chunk_size = 500
        total_deleted = 0
        for i in range(0, len(old_job_ids), chunk_size):
            chunk = old_job_ids[i:i + chunk_size]
            # Delete scores first (no cascade set)
            await session.execute(delete(JobScore).where(JobScore.job_id.in_(chunk)))
            # Delete applications (rejected/saved only — protected ones excluded above)
            await session.execute(delete(Application).where(Application.job_id.in_(chunk)))
            # Delete jobs
            result = await session.execute(delete(Job).where(Job.id.in_(chunk)))
            total_deleted += result.rowcount

        await session.commit()
        logger.info("Cleanup: deleted %d old jobs (cutoff=%s, protected=%d)",
                    total_deleted, cutoff.date(), len(protected_ids))
