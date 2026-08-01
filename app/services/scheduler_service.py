from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions
from app.config import settings
from app.database import async_session
from app.models.application import Application
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring.gemini_matcher import score_jobs_gemini
from app.scoring.matcher import score_jobs
from app.scoring.profile_hash import compute_profile_hash
from app.scoring.rules import matches_explicit_target_title, pre_filter
from app.services.backup_service import run_backup
from app.services.ops_service import record_ops_event
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids
from app.sources import (
    AdzunaSource,
    ArbeitnowSource,
    ArbeitsagenturSource,
    BerlinStartupJobsSource,
    GupyFeedSource,
    JobSpySource,
    JoobleSource,
    RemotiveSource,
    WatchlistSource,
    WTTJSource,
    XingSource,
)
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources.country_queries import expand_queries_for_country

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
    # Semantic indexer: fills missing job/profile embeddings in small batches.
    scheduler.add_job(
        _embed_index,
        "interval",
        hours=settings.embedding_index_interval_hours,
        id="embed_index",
        replace_existing=True,
    )
    scheduler.add_job(
        _embed_index,
        "date",
        run_date=datetime.now() + timedelta(seconds=90),
        id="embed_index_startup",
        replace_existing=True,
    )
    # Optional NVIDIA idle rescorer. NVIDIA remains available as the automatic
    # backfill fallback even when this periodic refresh job is disabled.
    if settings.nvidia_idle_rescore_enabled:
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
    # URL liveness check: daily at 04:00 UTC — HEAD-ping job postings to flag
    # closed listings. See app/services/url_checker.py.
    scheduler.add_job(
        _check_job_urls,
        "cron",
        hour=4,
        minute=0,
        id="check_job_urls",
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

        aggregator = JobAggregator(
            [
                AdzunaSource(),
                JobSpySource(),
                ArbeitnowSource(),
                RemotiveSource(),
                ArbeitsagenturSource(),
                XingSource(),
                BerlinStartupJobsSource(),
                WTTJSource(),
                JoobleSource(),
                GupyFeedSource(),
            ]
        )


        try:
            async with async_session() as session:
                # 1. Find all users with profiles to determine dynamic search scope
                users_result = await session.execute(
                    select(User).options(selectinload(User.profile)).where(User.is_active.is_(True))
                )
                users = users_result.scalars().all()

                # Ordered dedupe, NOT a set: profile order = user's priority ranking.
                # Sources cap their query lists (JobSpy top-N, Adzuna combo cap), so
                # with a set the "lucky" titles were arbitrary and reshuffled on every
                # container restart — most titles were never searched at all.
                dynamic_queries: list[str] = []
                dynamic_countries: list[str] = []
                seen_q: set[str] = set()
                seen_c: set[str] = set()

                for user in users:
                    if user.profile:
                        for title in (user.profile.target_titles or []):
                            norm = " ".join(title.split())  # collapse stray double spaces
                            if norm and norm.lower() not in seen_q:
                                seen_q.add(norm.lower())
                                dynamic_queries.append(norm)
                        for c in (user.profile.preferred_countries or []):
                            code = c.strip().lower()
                            if code and code not in seen_c:
                                seen_c.add(code)
                                dynamic_countries.append(code)

                # Fallbacks to defaults if nothing found in profiles
                final_queries = dynamic_queries if dynamic_queries else SCAN_QUERIES
                final_countries = dynamic_countries if dynamic_countries else ["de", "at", "nl", "ch", "be", "si", "sk", "ro", "hu"]
                country_queries = {
                    country: expand_queries_for_country(final_queries, country)
                    for country in final_countries
                    if country == "br"
                }

                params = SearchParams(
                    queries=final_queries,
                    countries=final_countries,
                    locations=[],
                    country_queries=country_queries,
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


def _is_hidden_country(job: Job, profile) -> bool:
    """Whether a job is excluded from default feed-style delivery."""
    hidden_countries = {
        str(country).strip().casefold()
        for country in (getattr(profile, "hidden_countries", None) or [])
        if str(country).strip()
    }
    return bool(job.country) and job.country.casefold() in hidden_countries


async def _score_and_notify(bot_app, user: User, all_jobs: list[Job], session):
    """Score new jobs for user, push top ones to Telegram."""
    # Scope "already scored" to rows whose profile_hash exactly matches the
    # current profile and scoring rules. NULL/legacy hashes are stale.
    profile_hash = compute_profile_hash(user.profile)
    scored_result = await session.execute(
        select(JobScore.job_id).where(
            JobScore.user_id == user.id,
            JobScore.profile_hash == profile_hash,
        )
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

    # Similarity is a ranking hint, never a rejection. Explicit target-title
    # matches lead even when an embedding is misleading.
    prioritized_jobs, deprioritized = await _semantic_priority_filter(
        session, user, profile_hash, new_jobs
    )
    if deprioritized:
        logger.info(
            "Real-time semantic-priority: %d jobs placed after stronger candidates "
            "(cosine < %.2f) for user %s",
            deprioritized, settings.semantic_skip_threshold, user.telegram_id,
        )

    # Low-similarity jobs stay eligible for a later run instead of being
    # permanently marked as scored.
    to_score = prioritized_jobs[:80]

    if settings.gemini_api_key:
        logger.info("Using Gemini for real-time scoring")
        scores = await score_jobs_gemini(to_score, user, session)
    else:
        scores = await score_jobs(to_score, user, session)

    # Find top results to push. Hidden countries are still collected and
    # scored for an auditable pilot, but must not leak into automatic
    # Telegram delivery (the default web feed applies the same preference).
    top_results = []
    for s in scores:
        if s.score >= TOP_SCORE_THRESHOLD:
            job = next((j for j in to_score if j.id == s.job_id), None)
            if job and not _is_hidden_country(job, user.profile):
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

    # Push to Telegram. ``Forbidden`` from python-telegram-bot means the
    # user blocked the bot or deleted the chat — there's no point retrying
    # for the next 6 weeks of scans. Detach by clearing telegram_id and
    # bail; the scoring rows we already wrote stay intact, the user can
    # re-link via /start later.
    from telegram.error import Forbidden  # noqa: PLC0415

    count = len(top_results)
    header = f"🔥 Найдено {count} {'новая топ-вакансия' if count == 1 else 'новых топ-вакансий'}!\n\nАвтоматический скан — только лучшие совпадения (score {TOP_SCORE_THRESHOLD}+)"
    pushed = 0
    try:
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
            pushed += 1
    except Forbidden:
        logger.warning(
            "Telegram Forbidden for user %s (id=%s) — clearing telegram_id",
            user.telegram_id, user.id,
        )
        user.telegram_id = None
        await session.commit()
        await record_ops_event(
            "telegram_forbidden", "warn",
            source="scheduler",
            message=f"user_id={user.id} cleared telegram_id (user blocked bot)",
        )
        return {
            "user_id": user.id, "telegram_id": None,
            "eligible_jobs": len(new_jobs), "scored_jobs": len(scores),
            "top_results": len(top_results), "pushed": pushed,
        }

    logger.info("Pushed %d top jobs to user %s", pushed, user.telegram_id)
    return {
        "user_id": user.id,
        "telegram_id": user.telegram_id,
        "eligible_jobs": len(new_jobs),
        "scored_jobs": len(scores),
        "top_results": len(top_results),
        "pushed": pushed,
    }


def _backfill_score_fn():
    """Return the appropriate scoring function for backfill.

    Gemini-first (changed 29.07.2026): the current Gemini 3.5 Flash Lite quota
    has enough headroom for both real-time and backfill scoring, while NVIDIA
    Build has become unreliable under bulk load (503s and read timeouts).

    Priority:
      1. Gemini Flash Lite — primary scorer while its circuit breaker is closed.
      2. NVIDIA Build      — automatic fallback when Gemini is unavailable.
      3. Claude            — last resort.
    """
    if settings.gemini_api_key:
        from app.scoring.gemini_matcher import is_gemini_available, score_jobs_gemini  # noqa: PLC0415
        if is_gemini_available():
            logger.debug("Backfill scorer: using Gemini (%s)", settings.gemini_scoring_model)
            return score_jobs_gemini
        logger.warning("Backfill scorer: Gemini breaker open — trying NVIDIA fallback")

    if settings.nvidia_api_key:
        from app.scoring.nvidia_matcher import score_jobs_nvidia  # noqa: PLC0415
        logger.debug("Backfill scorer: using NVIDIA fallback (%s)", settings.nvidia_model)
        return score_jobs_nvidia

    logger.debug("Backfill scorer: using Claude (%s)", settings.claude_model)
    return score_jobs


def _order_by_semantic_priority(
    candidates: list[Job],
    similarities: dict[int, float],
    threshold: float,
    profile: UserProfile | None,
) -> tuple[list[Job], int]:
    """Order candidates without removing any of them.

    Exact targets lead, then sufficiently similar jobs, jobs awaiting an
    embedding, and finally low-similarity jobs. Input order breaks ties.
    """
    decorated: list[tuple[int, float, int, Job]] = []
    low_count = 0
    for index, job in enumerate(candidates):
        similarity = similarities.get(job.id)
        if matches_explicit_target_title(job.title, profile):
            group = 0
        elif similarity is not None and similarity >= threshold:
            group = 1
        elif similarity is None:
            group = 2
        else:
            group = 3
            low_count += 1
        decorated.append((group, -(similarity or 0.0), index, job))

    decorated.sort(key=lambda item: item[:3])
    return [item[3] for item in decorated], low_count


async def _semantic_priority_filter(
    session,
    user: User,
    profile_hash: str | None,
    candidates: list[Job],
) -> tuple[list[Job], int]:
    """Prioritise candidates by cosine similarity without rejecting any.

    Returns ``(ordered_jobs, low_similarity_count)``. Falls through when:
    * feature disabled (``semantic_skip_enabled=False``)
    * user profile has no embedding yet
    * not on PostgreSQL (no pgvector)
    * candidate list is empty

    On a database error it returns the input unchanged.
    """
    if not settings.semantic_skip_enabled or not candidates:
        return candidates, 0

    # Profile must have an embedding. Bail fast if not — embed_index will
    # populate it on its next tick.
    profile = user.profile
    if profile is None:
        return candidates, 0
    # The embedding must also be FRESH (built from the current profile).
    # A stale vector — e.g. countries changed but embed_index hasn't re-run —
    # would wrongly zero-out jobs from newly added regions. Better to send
    # everything to AI for one cycle than to skip on outdated similarity.
    has_profile_emb = await session.execute(
        text(
            "SELECT 1 FROM user_profiles"
            " WHERE id = :pid AND embedding IS NOT NULL"
            " AND embedding_profile_hash = :ph"
        ),
        {"pid": profile.id, "ph": profile_hash},
    )
    if has_profile_emb.scalar() is None:
        return candidates, 0

    job_ids = [j.id for j in candidates]
    threshold = settings.semantic_skip_threshold

    # pgvector cosine distance is 1 - cosine similarity.
    try:
        result = await session.execute(
            text(
                """
                SELECT j.id, 1 - (j.embedding <=> p.embedding) AS similarity
                FROM jobs j
                CROSS JOIN user_profiles p
                WHERE p.id = :pid
                  AND j.id = ANY(:ids)
                  AND j.embedding IS NOT NULL
                """
            ),
            {"pid": profile.id, "ids": job_ids},
        )
        sims = {row[0]: float(row[1]) for row in result.all()}
    except SQLAlchemyError as exc:
        logger.warning("semantic priority query failed for user %s: %s", user.id, exc)
        return candidates, 0

    return _order_by_semantic_priority(candidates, sims, threshold, profile)


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
            select(User).options(selectinload(User.profile)).where(User.is_active.is_(True))
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

                # "Already scored" requires the current profile/rules hash.
                # Mismatched and legacy NULL hashes return to the queue.
                profile_hash = compute_profile_hash(user.profile)
                scored_result = await session.execute(
                    select(JobScore.job_id).where(
                        JobScore.user_id == user.id,
                        JobScore.profile_hash == profile_hash,
                    )
                )
                already_scored_ids = {row[0] for row in scored_result.fetchall()}

                hidden_ids = await get_hidden_job_ids(user.id, session)
                hidden_hashes = await get_hidden_dedup_hashes(user.id, session)

                need_ai_t1: list[Job] = []   # director/head/VP + domain
                need_ai_t2: list[Job] = []   # plain manager + domain (lower priority)
                skip_rows: list[dict] = []

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
                        # Hard reject — mark score=0 so it never re-enters the queue.
                        # Profile rules are authoritative. A non-NULL analysis
                        # prevents the legacy Gemini "second opinion" pass from
                        # overriding explicit user exclusions.
                        skip_rows.append({
                            "job_id": job.id,
                            "user_id": user.id,
                            "score": 0,
                            "ai_analysis": "Filtered by current profile rules",
                            "profile_hash": profile_hash,
                            "model_version": "prefilter",
                        })

                # Bulk UPSERT hard rejects (no API calls) — cap at 2000 per run.
                # ON CONFLICT DO UPDATE handles both mismatched hashes and
                # legacy NULL hashes. Matching rows remain untouched.
                if skip_rows:
                    capped = skip_rows[:2000]
                    stmt = pg_insert(JobScore).values(capped)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["job_id", "user_id"],
                        set_={
                            "score": stmt.excluded.score,
                            "ai_analysis": stmt.excluded.ai_analysis,
                            "scored_at": func.now(),
                            "profile_hash": stmt.excluded.profile_hash,
                            "model_version": stmt.excluded.model_version,
                        },
                        where=or_(
                            JobScore.profile_hash.is_(None),
                            JobScore.profile_hash != stmt.excluded.profile_hash,
                        ),
                    )
                    await session.execute(stmt)
                    await session.commit()
                    logger.info(
                        "Backfill: marked %d jobs as rejected (pre-filter) for user %s",
                        len(capped), user.telegram_id,
                    )

                # Similarity only orders candidates; it cannot discard them.
                need_ai_t1, t1_deprioritized = await _semantic_priority_filter(
                    session, user, profile_hash, need_ai_t1
                )
                need_ai_t2, t2_deprioritized = await _semantic_priority_filter(
                    session, user, profile_hash, need_ai_t2
                )
                if t1_deprioritized + t2_deprioritized:
                    logger.info(
                        "Backfill semantic-priority [%s]: %d t1 + %d t2 jobs placed after "
                        "stronger candidates (cosine < %.2f) for user %s",
                        backend, t1_deprioritized, t2_deprioritized,
                        settings.semantic_skip_threshold, user.telegram_id,
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
    if not settings.nvidia_idle_rescore_enabled or not settings.nvidia_api_key:
        return

    from app.scoring.nvidia_matcher import idle_rescore_for_user  # noqa: PLC0415

    country = settings.nvidia_country.lower()
    cutoff = datetime.now() - timedelta(days=settings.job_max_age_days)

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active.is_(True))
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


async def _embed_index():
    """Backfill pgvector embeddings for jobs and profiles in small batches."""
    if not settings.embedding_enabled or not settings.gemini_api_key:
        return

    from app.services.embedding_service import index_missing_embeddings  # noqa: PLC0415

    async with async_session() as session:
        try:
            counts = await index_missing_embeddings(session)
        except Exception as exc:
            logger.exception("Embedding indexer failed: %s", exc)
            await record_ops_event(
                "embedding_index",
                "error",
                source="gemini_embedding",
                message=f"{type(exc).__name__}: {exc}",
            )
            return

    if counts.get("skipped"):
        return
    logger.info("Embedding indexer finished: %s", counts)
    await record_ops_event(
        "embedding_index",
        "success",
        source="gemini_embedding",
        message=f"jobs={counts['jobs']} profiles={counts['profiles']}",
        payload=counts,
    )


async def _check_job_urls():
    """Daily HEAD-ping liveness check on Job.url. Runs at 04:00 UTC.

    Drains the entire eligible queue: loops ``run_url_check_pass`` until either
    (a) no more jobs need checking, or (b) the wall-clock budget is exceeded.
    The budget guarantees we're done before the user's morning rush even if a
    sudden import added thousands of new jobs overnight.

    Each individual pass picks ``url_check_per_run`` (default 500) oldest-
    checked jobs, HEAD-pings them concurrently (cap ``url_check_concurrency``,
    per-host throttle ``url_check_per_host_delay``), and commits results in
    one transaction.
    """
    if not settings.url_check_enabled:
        logger.info("URL liveness check disabled (url_check_enabled=False)")
        return

    # Wall-clock cap. 04:00 UTC + ≤3h = 07:00 UTC = 09:00 Berlin worst case.
    # On steady-state queues each pass is short and we exit before then.
    DRAIN_BUDGET_SECONDS = 3 * 3600

    logger.info("URL liveness check started")
    started = time.perf_counter()
    from app.services.url_checker import run_url_check_pass  # noqa: PLC0415

    total = {"checked": 0, "active": 0, "closed": 0, "unreachable": 0, "skipped": 0, "soft_404": 0, "passes": 0}
    error_msg: str | None = None

    while True:
        if time.perf_counter() - started > DRAIN_BUDGET_SECONDS:
            logger.warning("URL drain time budget exceeded — stopping")
            break

        async with async_session() as session:
            try:
                counts = await run_url_check_pass(session)
            except Exception as exc:
                logger.exception("URL check pass failed")
                error_msg = f"{type(exc).__name__}: {exc}"
                break

        total["passes"] += 1
        for k in ("checked", "active", "closed", "unreachable", "skipped", "soft_404"):
            total[k] += counts.get(k, 0)

        if counts["checked"] == 0:
            # Queue drained — nothing left to look at.
            break

    elapsed = time.perf_counter() - started
    logger.info("URL liveness check finished in %.1fs: %s", elapsed, total)

    if error_msg:
        await record_ops_event(
            "url_check", "error",
            source="url_checker",
            message=f"after passes={total['passes']} checked={total['checked']}: {error_msg}",
            payload=total,
        )
    else:
        await record_ops_event(
            "url_check", "success",
            source="url_checker",
            message=(
                f"passes={total['passes']} checked={total['checked']} "
                f"active={total['active']} closed={total['closed']} "
                f"soft_404={total['soft_404']} "
                f"unreachable={total['unreachable']} elapsed={elapsed:.1f}s"
            ),
            payload=total,
        )


async def _watchlist_scan(bot_app):
    """For each user with target_companies, fetch jobs from those companies and notify."""
    logger.info("Watchlist scan started")

    from app.sources.aggregator import JobAggregator

    async with async_session() as session:
        users_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.is_active.is_(True))
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
