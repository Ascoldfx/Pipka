"""Re-evaluate recent, unactioned vacancies for selected countries.

Examples:
    python -m scripts.rescore_countries --user-id 1 --countries ae sa qa --dry-run
    python -m scripts.rescore_countries --user-id 1 --countries ae sa qa --backend gemini

Hard pre-filter rejects are refreshed without an AI call. Eligible tier-1 and
tier-2 vacancies are sent to the selected scorer. Applied/rejected/saved jobs
and closed URLs are left untouched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.job import Job, JobScore
from app.models.user import User
from app.scoring.profile_hash import compute_profile_hash
from app.scoring.rules import pre_filter
from app.services.tracker_service import get_hidden_dedup_hashes, get_hidden_job_ids


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--countries", nargs="+", required=True)
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument(
        "--backend",
        choices=("auto", "gemini", "nvidia", "claude"),
        default="auto",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum AI-eligible vacancies this run; 0 means all.",
    )
    parser.add_argument(
        "--reuse-from-hash",
        default=None,
        help=(
            "Adopt existing AI scores from this audited profile hash for jobs "
            "that still pass current rules; changed hard rejects are refreshed."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _score_function(backend: str):
    if backend == "gemini":
        from app.scoring.gemini_matcher import score_jobs_gemini

        return score_jobs_gemini
    if backend == "nvidia":
        from app.scoring.nvidia_matcher import score_jobs_nvidia

        return score_jobs_nvidia
    if backend == "claude":
        from app.scoring.matcher import score_jobs

        return score_jobs

    from app.services.scheduler_service import _backfill_score_fn

    return _backfill_score_fn()


async def _upsert_rejects(
    user_id: int,
    profile_hash: str,
    jobs: list[Job],
    session,
) -> None:
    for offset in range(0, len(jobs), 1000):
        rows = [
            {
                "job_id": job.id,
                "user_id": user_id,
                "score": 0,
                "ai_analysis": "Filtered by current profile rules",
                "profile_hash": profile_hash,
                "model_version": "prefilter",
            }
            for job in jobs[offset : offset + 1000]
        ]
        if not rows:
            continue
        stmt = pg_insert(JobScore).values(rows)
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


async def _reuse_eligible_scores(
    user_id: int,
    old_hash: str,
    current_hash: str,
    jobs: list[Job],
    session,
) -> int:
    """Adopt audited old AI scores for jobs that still pass current rules."""
    adopted = 0
    for offset in range(0, len(jobs), 1000):
        job_ids = [job.id for job in jobs[offset : offset + 1000]]
        if not job_ids:
            continue
        result = await session.execute(
            update(JobScore)
            .where(
                JobScore.user_id == user_id,
                JobScore.job_id.in_(job_ids),
                JobScore.profile_hash == old_hash,
                or_(
                    JobScore.model_version.like("gemini:%"),
                    JobScore.model_version.like("nvidia:%"),
                    JobScore.model_version.like("claude:%"),
                ),
            )
            .values(profile_hash=current_hash, scored_at=func.now())
        )
        adopted += result.rowcount or 0
        await session.commit()
    return adopted


async def _run(args: argparse.Namespace) -> None:
    countries = sorted(
        {
            value.strip().lower()
            for value in args.countries
            if value.strip()
        }
    )
    cutoff = datetime.now() - timedelta(days=args.days)

    async with async_session() as session:
        user_result = await session.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.id == args.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None or user.profile is None:
            raise SystemExit(f"User/profile {args.user_id} not found")

        profile_hash = compute_profile_hash(user.profile)
        if profile_hash is None:
            raise SystemExit("Current profile hash is unavailable")

        jobs_result = await session.execute(
            select(Job)
            .where(
                Job.country.in_(countries),
                Job.scraped_at >= cutoff,
                or_(Job.url_status.is_(None), Job.url_status == "active"),
            )
            .order_by(Job.scraped_at.desc(), Job.id.desc())
        )
        jobs = list(jobs_result.scalars().all())

        hidden_ids = await get_hidden_job_ids(user.id, session)
        hidden_hashes = await get_hidden_dedup_hashes(user.id, session)
        current_result = await session.execute(
            select(JobScore.job_id).where(
                JobScore.user_id == user.id,
                JobScore.profile_hash == profile_hash,
                JobScore.job_id.in_([job.id for job in jobs]),
            )
        )
        current_ids = set(current_result.scalars().all())

        pending = [
            job
            for job in jobs
            if job.id not in current_ids
            and job.id not in hidden_ids
            and job.dedup_hash not in hidden_hashes
        ]
        tier1: list[Job] = []
        tier2: list[Job] = []
        rejected: list[Job] = []
        buckets: dict[str, int] = {}
        for job in pending:
            passed, bucket = pre_filter(job, user.profile)
            buckets[bucket] = buckets.get(bucket, 0) + 1
            if passed and bucket in {"high", "medium"}:
                tier1.append(job)
            elif not passed and bucket == "manager_tier2":
                tier2.append(job)
            else:
                rejected.append(job)

        eligible = tier1 + tier2
        selected = eligible[: args.limit] if args.limit > 0 else eligible
        summary = {
            "user_id": user.id,
            "countries": countries,
            "days": args.days,
            "profile_hash": profile_hash,
            "jobs_in_scope": len(jobs),
            "already_current": len(current_ids),
            "hidden_or_actioned": len(jobs) - len(current_ids) - len(pending),
            "pending": len(pending),
            "buckets": buckets,
            "prefilter_rejects": len(rejected),
            "ai_eligible": len(eligible),
            "ai_selected": len(selected),
            "backend": args.backend,
            "dry_run": args.dry_run,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if args.dry_run:
            return

        await _upsert_rejects(user.id, profile_hash, rejected, session)
        if args.reuse_from_hash:
            adopted = await _reuse_eligible_scores(
                user.id,
                args.reuse_from_hash,
                profile_hash,
                eligible,
                session,
            )
            result = {
                **summary,
                "prefilter_refreshed": len(rejected),
                "ai_reused": adopted,
                "ai_remaining": len(eligible) - adopted,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        score_fn = _score_function(args.backend)
        await score_fn(selected, user, session)

        eligible_ids = [job.id for job in eligible]
        refreshed_result = await session.execute(
            select(JobScore.job_id).where(
                JobScore.user_id == user.id,
                JobScore.profile_hash == profile_hash,
                JobScore.job_id.in_(eligible_ids),
            )
        )
        refreshed_ids = set(refreshed_result.scalars().all())
        result = {
            **summary,
            "backend_used": score_fn.__name__,
            "prefilter_refreshed": len(rejected),
            "ai_current_after_run": len(refreshed_ids),
            "ai_remaining": len(eligible_ids) - len(refreshed_ids),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
