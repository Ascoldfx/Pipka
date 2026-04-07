from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

NEGATIVE_KEYWORDS = [
    "ausbildung", "student", "praktikum", "azubi", "trainee",
    "werkstudent", "junior", "intern", "duales studium",
]


class JobAggregator:
    def __init__(self, sources: list[JobSource]):
        self.sources = sources

    async def search(self, params: SearchParams, session: AsyncSession) -> list[Job]:
        tasks = [source.search(params) for source in self.sources]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_jobs: list[RawJob] = []
        for i, result in enumerate(all_results):
            if isinstance(result, Exception):
                logger.error("Source %s failed: %s", self.sources[i].source_name, result)
                continue
            raw_jobs.extend(result)

        logger.info("Aggregated %d raw jobs from %d sources", len(raw_jobs), len(self.sources))

        # Deduplicate
        seen_hashes: set[str] = set()
        unique: list[RawJob] = []
        for job in raw_jobs:
            if job.dedup_hash not in seen_hashes:
                seen_hashes.add(job.dedup_hash)
                unique.append(job)

        logger.info("After dedup: %d unique jobs", len(unique))

        # Filter
        cutoff = datetime.now() - timedelta(days=params.max_age_days)
        filtered: list[RawJob] = []
        for job in unique:
            if _is_negative(job.title):
                continue
            if job.posted_at and job.posted_at < cutoff:
                continue
            filtered.append(job)

        logger.info("After filter: %d jobs", len(filtered))

        # Upsert into DB
        db_jobs: list[Job] = []
        for raw in filtered:
            existing = await session.execute(select(Job).where(Job.dedup_hash == raw.dedup_hash))
            job_row = existing.scalar_one_or_none()
            if job_row is None:
                job_row = Job(
                    external_id=raw.external_id,
                    source=raw.source,
                    title=raw.title,
                    company_name=raw.company_name,
                    location=raw.location,
                    country=raw.country,
                    description=raw.description,
                    salary_min=raw.salary_min,
                    salary_max=raw.salary_max,
                    salary_currency=raw.salary_currency,
                    url=raw.url,
                    is_remote=raw.is_remote,
                    posted_at=raw.posted_at,
                    raw_data=raw.raw_data,
                    dedup_hash=raw.dedup_hash,
                )
                session.add(job_row)
            db_jobs.append(job_row)

        await session.commit()
        # Sort: newest first
        db_jobs.sort(key=lambda j: j.posted_at or datetime.min, reverse=True)
        return db_jobs


def _is_negative(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in NEGATIVE_KEYWORDS)
