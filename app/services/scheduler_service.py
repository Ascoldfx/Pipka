from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import async_session
from app.models.application import SearchSubscription
from app.models.user import User
from app.services.job_service import search_and_score
from app.sources.adzuna import AdzunaSource
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources.jobspy_source import JobSpySource

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def start_scheduler(bot_app):
    """Start the scheduler with a periodic check for subscriptions."""
    scheduler.add_job(
        _run_subscriptions,
        "interval",
        hours=1,
        args=[bot_app],
        id="subscription_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started")


async def _run_subscriptions(bot_app):
    """Check all active subscriptions and send digests."""
    async with async_session() as session:
        result = await session.execute(
            select(SearchSubscription).where(SearchSubscription.is_active == True)
        )
        subs = result.scalars().all()

        for sub in subs:
            try:
                # Simple schedule check: run daily
                if sub.last_run and (datetime.now() - sub.last_run).total_seconds() < 23 * 3600:
                    continue

                user_result = await session.execute(select(User).where(User.id == sub.user_id))
                user = user_result.scalar_one_or_none()
                if not user:
                    continue

                qp = sub.query_params or {}
                params = SearchParams(
                    queries=qp.get("queries", ["Supply Chain"]),
                    countries=qp.get("countries", ["de"]),
                    locations=qp.get("locations", []),
                )

                aggregator = JobAggregator([AdzunaSource(), JobSpySource()])
                results = await search_and_score(aggregator, params, user, session)

                if results:
                    text = f"📬 Дайджест: {sub.name}\nНайдено {len(results)} вакансий:\n\n"
                    for job, score, verdict in results[:10]:
                        text += f"{'🟢' if score >= 70 else '🟡' if score >= 40 else '🔴'} {score} — {job.title} @ {job.company_name or '?'}\n"
                        if job.url:
                            text += f"  {job.url}\n"

                    await bot_app.bot.send_message(
                        chat_id=user.telegram_id, text=text,
                    )

                sub.last_run = datetime.now()
                await session.commit()
                logger.info("Digest sent for subscription %s to user %s", sub.id, user.telegram_id)

            except Exception as e:
                logger.error("Subscription %s failed: %s", sub.id, e)
