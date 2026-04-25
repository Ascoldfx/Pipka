from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions
from app.config import settings
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.gemini_matcher import score_jobs_gemini
from app.scoring.matcher import score_jobs
from app.scoring.rules import pre_filter
from app.services.backup_service import run_backup
from app.services.ops_service import record_ops_event
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources import AdzunaSource, JobSpySource, ArbeitnowSource, RemotiveSource, ArbeitsagenturSource, XingSource, WatchlistSource, BerlinStartupJobsSource, WTTJSource, JoobleSource

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_scan_lock = asyncio.Lock()

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


def is_scan_running() -> bool:
    return _scan_lock.locked()


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
    # Daily DB backup at 02:30 UTC — pg_dump → gzip → local + optional B2
    scheduler.add_job(
        _daily_backup,
        "cron",
        hour=2,
        minute=30,
        id="daily_backup",
        replace_existing=True,
    )
    # Backfill scorer: every 2 hours — score existing unscored jobs for each user
    scheduler.add_job(
        _backfill_score,
        "interval",
        hours=2,
        id="backfill_score",
        replace_existing=True,
    )
    # NVIDIA idle rescorer: every 30 min — runs only when Gemini queue drained
    scheduler.add_job(
        _nvidia_idle_rescore,
        "interval",
        minutes=30,
        id="nvidia_idle_rescore",
        replace_existing=True,
    )
    # Watchlist scan: every 6 hours — search for jobs at target companies per user
    scheduler.add_job(
        _watchlist_scan,
        "interval",
        hours=6,
        args=[bot_app],
        id="watchlist_scan",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Background scanner started (every 3 hours, first scan in 30s)")


async def _background_scan(bot_app, trigger: str = "scheduled"):
    """Scan all sources, score only NEW jobs, push top results to Telegram."""
    if _scan_lock.locked():
        # Normal — manual scan still running when scheduled one fires; just skip silently
        logger.info("Skipping %s scan — previous scan still in progress", trigger)
        return

    async with _scan_lock:
        logger.info("Background scan started (%s)", trigger)
        started_at = datetime.now()
        started_perf = time.perf_counter()

        aggregator = JobAggregator([AdzunaSource(), JobSpySource(), ArbeitnowSource(), RemotiveSource(), ArbeitsagenturSource(), XingSource(), BerlinStartupJobsSource(), WTTJSource(), JoobleSource()])


        try:
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

                user_summaries = []
                for user in users:
                    if not user.profile:
                        continue

                    try:
                        summary = await _score_and_notify(bot_app, user, all_jobs, session)
                        user_summaries.append(summary)
                    except Exception as e:
                        logger.error("Background scan failed for user %s: %s", user.telegram_id, e)
                        user_summaries.append(
                            {
                                "user_id": user.id,
                                "telegram_id": user.telegram_id,
                                "eligible_jobs": 0,
                                "scored_jobs": 0,
                                "top_results": 0,
                                "pushed": 0,
                                "error": str(e)[:200],
                            }
                        )

                duration_seconds = round(time.perf_counter() - started_perf, 2)
                await record_ops_event(
                    "scan",
                    "success",
                    source=trigger,
                    message=f"Scan finished in {duration_seconds}s",
                    payload={
                        "started_at": started_at.isoformat(),
                        "duration_seconds": duration_seconds,
                        "query_count": len(final_queries),
                        "country_count": len(final_countries),
                        "db_jobs_after_scan": len(all_jobs),
                        "aggregator": aggregator.last_stats,
                        "users": user_summaries,
                    },
                )
        except Exception as e:
            duration_seconds = round(time.perf_counter() - started_perf, 2)
            await record_ops_event(
                "scan",
                "error",
                source=trigger,
                message=f"Scan failed after {duration_seconds}s: {str(e)[:180]}",
                payload={
                    "started_at": started_at.isoformat(),
                    "duration_seconds": duration_seconds,
                },
            )
            raise

    logger.info("Background scan completed (%s)", trigger)


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
        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "eligible_jobs": 0,
            "scored_jobs": 0,
            "top_results": 0,
            "pushed": 0,
        }

    logger.info("Scoring %d new jobs for user %s", len(new_jobs), user.telegram_id)

    # Score only new jobs (max 80 per run to control costs/limits)
    to_score = new_jobs[:80]
    
    if settings.gemini_api_key:
        logger.info("Using Gemini for real-time scoring")
        scores = await score_jobs_gemini(to_score, user, session)
    else:
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
        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "eligible_jobs": len(new_jobs),
            "scored_jobs": len(scores),
            "top_results": 0,
            "pushed": 0,
        }

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
    return {
        "user_id": user.id,
        "telegram_id": user.telegram_id,
        "eligible_jobs": len(new_jobs),
        "scored_jobs": len(scores),
        "top_results": len(top_results),
        "pushed": min(count, 10),
    }


def _backfill_score_fn():
    """Return the appropriate scoring function for backfill.

    Priority:
      1. Gemini Flash (free) — if key set AND circuit breaker is closed.
      2. NVIDIA Build (free) — if Gemini is exhausted/disabled and NVIDIA key is set.
      3. Claude (paid)       — last resort.
    """
    if settings.gemini_api_key:
        from app.scoring.gemini_matcher import is_gemini_available, score_jobs_gemini  # noqa: PLC0415
        if is_gemini_available():
            logger.debug("Backfill scorer: using Gemini Flash (%s)", settings.gemini_model)
            return score_jobs_gemini
        logger.warning("Backfill scorer: Gemini breaker open — falling back")

    if settings.nvidia_api_key:
        from app.scoring.nvidia_matcher import score_jobs_nvidia  # noqa: PLC0415
        logger.info("Backfill scorer: using NVIDIA Build (%s)", settings.nvidia_model)
        return score_jobs_nvidia

    logger.debug("Backfill scorer: using Claude (%s)", settings.claude_model)
    return score_jobs


async def _backfill_score():
    """Score existing DB jobs that haven't been scored yet for each user.

    Runs every 2 hours. Two-pass approach:
      1. Pre-filter rejects → immediately write JobScore(score=0) — no Claude call needed.
         This drains the "unscored" queue for irrelevant jobs without burning API credits.
      2. Pre-filter passes  → send up to 500 per run to the AI scorer
         (Gemini Flash if GEMINI_API_KEY is set, Claude otherwise).
    """
    _score_fn = _backfill_score_fn()
    backend = _score_fn.__name__.replace("score_jobs_", "").replace("score_jobs", "claude") or "claude"
    logger.info("Backfill scorer started (backend=%s)", backend)

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        for user in users:
            if not user.profile:
                continue
            try:
                cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)
                all_jobs_result = await session.execute(
                    select(Job).where(Job.scraped_at >= cutoff)
                )
                all_jobs = all_jobs_result.scalars().all()

                scored_result = await session.execute(
                    select(JobScore.job_id).where(JobScore.user_id == user.id)
                )
                already_scored_ids = {row[0] for row in scored_result.fetchall()}

                hidden_ids = await get_hidden_job_ids(user.id, session)
                hidden_hashes = await get_hidden_dedup_hashes(user.id, session)

                need_ai_t1: list[Job] = []   # director/head/VP + domain
                need_ai_t2: list[Job] = []   # plain manager + domain (lower priority)
                skip_batch: list[JobScore] = []

                for job in all_jobs:
                    if job.id in already_scored_ids:
                        continue
                    if job.id in hidden_ids or job.dedup_hash in hidden_hashes:
                        continue
                    passed, bucket = pre_filter(job, user.profile)
                    if passed and bucket in ("high", "medium"):
                        need_ai_t1.append(job)
                    elif not passed and bucket == "manager_tier2":
                        need_ai_t2.append(job)
                    else:
                        # Hard reject — mark score=0 so it never re-enters the queue
                        skip_batch.append(JobScore(
                            job_id=job.id,
                            user_id=user.id,
                            score=0,
                            ai_analysis=None,
                        ))

                # Bulk-insert hard rejects (no API calls) — cap at 2000 per run
                if skip_batch:
                    for rec in skip_batch[:2000]:
                        session.add(rec)
                    await session.commit()
                    logger.info(
                        "Backfill: marked %d jobs as rejected (pre-filter) for user %s",
                        min(len(skip_batch), 2000), user.telegram_id,
                    )

                # Tier 1 first: director / head of / VP
                if need_ai_t1:
                    to_score = need_ai_t1[:1000]
                    logger.info(
                        "Backfill tier1 [%s]: AI-scoring %d director-level jobs for user %s",
                        backend, len(to_score), user.telegram_id,
                    )
                    await _score_fn(to_score, user, session)
                    continue  # come back next run for tier2

                # Tier 2: manager-level, only when tier1 is fully cleared
                if need_ai_t2:
                    to_score = need_ai_t2[:1000]
                    logger.info(
                        "Backfill tier2 [%s]: AI-scoring %d manager-level jobs for user %s",
                        backend, len(to_score), user.telegram_id,
                    )
                    await _score_fn(to_score, user, session)
                    continue  # recheck only after tier2 is also empty

                # Both queues empty → safety recheck of pre-filter rejects
                # Sends score=0/ai_analysis=NULL jobs to Gemini for a second opinion.
                # Catches anything the rule-based filter may have wrongly rejected.
                if settings.gemini_api_key:
                    from app.scoring.gemini_matcher import recheck_zero_scores  # noqa: PLC0415
                    checked, upgraded = await recheck_zero_scores(user, session, limit=500)
                    if checked:
                        logger.info(
                            "Backfill recheck: %d pre-filter rejects checked, %d upgraded for user %s",
                            checked, upgraded, user.telegram_id,
                        )

            except Exception as e:
                logger.error("Backfill scorer failed for user %s: %s", user.telegram_id, e)

    logger.info("Backfill scorer completed")


async def _nvidia_idle_rescore():
    """Rescore DE jobs via NVIDIA Build (Gemma) when the Gemini queue is drained.

    Runs every 30 min but is a no-op unless:
      • `NVIDIA_API_KEY` is set in .env
      • the user has no unscored jobs in the last 45 days for country=DE

    Two priorities per user:
      (a) recheck pre-filter rejects (score=0, ai_analysis IS NULL)
      (b) refresh stale successful scores (score > 0, scored_at older than N days)
    """
    if not settings.nvidia_api_key:
        return

    from app.scoring.nvidia_matcher import idle_rescore_for_user  # noqa: PLC0415

    country = settings.nvidia_country.lower()
    cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        for user in users:
            if not user.profile:
                continue

            # Guard: only fire when the Gemini backfill queue is fully drained for DE.
            unscored_count_result = await session.execute(
                select(func.count(Job.id)).where(
                    Job.country == country,
                    Job.scraped_at >= cutoff,
                    ~Job.id.in_(
                        select(JobScore.job_id).where(JobScore.user_id == user.id)
                    ),
                )
            )
            unscored = unscored_count_result.scalar() or 0
            if unscored > 0:
                logger.debug(
                    "NVIDIA rescore skipped for user %s: %d unscored in queue",
                    user.telegram_id, unscored,
                )
                continue

            try:
                checked, upgraded, refreshed = await idle_rescore_for_user(user, session)
                if checked or refreshed:
                    await record_ops_event(
                        "nvidia_rescore", "success", source="nvidia",
                        message=f"user={user.telegram_id} checked={checked} upgraded={upgraded} refreshed={refreshed}",
                    )
            except Exception as exc:
                logger.error("NVIDIA idle rescore failed for user %s: %s", user.telegram_id, exc)
                await record_ops_event(
                    "nvidia_rescore", "error", source="nvidia",
                    message=f"user={user.telegram_id} {type(exc).__name__}: {exc}",
                )


async def _watchlist_scan(bot_app):
    """For each user with target_companies, fetch jobs from those companies and notify."""
    logger.info("Watchlist scan started")

    from app.sources.aggregator import JobAggregator

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active == True)
        )
        users = users_result.scalars().all()

        for user in users:
            if not user.profile:
                continue
            companies = getattr(user.profile, "target_companies", None) or []
            if not companies:
                continue
            if not user.telegram_id:
                continue

            try:
                countries = user.profile.preferred_countries or ["de"]
                params = SearchParams(
                    queries=companies,      # WatchlistSource treats queries as company names
                    countries=countries,
                    locations=[],
                )
                # Aggregator handles dedup, filtering, and DB upsert
                aggregator = JobAggregator([WatchlistSource()])
                stored_jobs = await aggregator.search(params, session)

                if not stored_jobs:
                    logger.info("Watchlist: no jobs found for user %s (%d companies)", user.telegram_id, len(companies))
                    continue

                logger.info("Watchlist: %d jobs found for user %s", len(stored_jobs), user.telegram_id)
                await _score_and_notify(bot_app, user, stored_jobs, session)

            except Exception as e:
                logger.error("Watchlist scan failed for user %s: %s", user.telegram_id, e)

    logger.info("Watchlist scan completed")


async def _daily_backup():
    """Daily DB backup at 02:30 UTC. Saves gzipped pg_dump to /app/data/backups/ (keeps last 7)."""
    from pathlib import Path  # noqa: PLC0415

    try:
        path = await run_backup()
        name = Path(path).name if path else "skipped"
        await record_ops_event("backup", "success", message=name)
        logger.info("Daily backup OK: %s", name)
    except Exception as e:
        logger.error("Daily backup failed: %s", e)
        await record_ops_event("backup", "error", message=str(e)[:250])


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
