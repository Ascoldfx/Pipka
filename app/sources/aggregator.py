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

# Phrases in title or description that indicate restricted positions
EXCLUSION_PHRASES = [
    "schwerbehindert",
    "schwerbehinderung",
    "ausschließlich für schwerbehinderte",
    "ausschliesslich für schwerbehinderte",
    "exclusively for severely disabled",
    "тяжелыми формами инвалидности",
    "gleichgestellte",
    "nur für schwerbehinderte",
]

# German C1+ / native required — auto-reject (candidate has B1)
GERMAN_C1_REQUIRED = [
    # German level markers
    "deutsch c1", "deutsch c2", "german c1", "german c2",
    "deutschkenntnisse c1", "deutschkenntnisse c2",
    # Verhandlungssicher / fluent
    "verhandlungssicheres deutsch", "verhandlungssicher deutsch",
    "verhandlungssichere deutschkenntnisse",
    # Native
    "deutsch als muttersprache", "deutsch muttersprachlich",
    "muttersprachliche deutschkenntnisse", "muttersprachliches deutsch",
    "deutsch auf muttersprachniveau",
    # Fließend
    "fließende deutschkenntnisse", "fliessende deutschkenntnisse",
    "fließend deutsch", "fliessend deutsch",
    "fließendes deutsch", "fließend in deutsch",
    # Perfekt / sehr gut
    "perfekte deutschkenntnisse", "perfektes deutsch",
    "sehr gute deutschkenntnisse", "sehr guten deutschkenntnissen",
    "exzellente deutschkenntnisse",
    # English versions
    "german native", "native german", "native-level german",
    "fluent german", "fluent in german", "german fluency",
    "fluency in german", "proficient in german",
    "german & english fluency", "german and english fluency",
    "fluent in both german and english",
    "business fluent german", "business-fluent german",
    # Combined patterns
    "german (native", "german (fluent", "german (c1", "german (c2",
    "deutsch (verhandlungssicher", "deutsch (fließend",
    "deutsch (muttersprachlich", "deutsch (c1", "deutsch (c2",
    # Proficiency patterns
    "proficiency in written and spoken german",
    "proficiency in german and english",
    "proficient in german and english",
    "written and spoken german",
    "german and english required",
    "german language skills required",
    "strong german language",
    "excellent german",
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
            if _is_negative(job):
                continue
            if job.posted_at and job.posted_at < cutoff:
                continue
            if _is_wrong_location(job):
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


US_LOCATIONS = [
    ", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct", ", de", ", fl",
    ", ga", ", hi", ", id", ", il", ", in", ", ia", ", ks", ", ky", ", la",
    ", me", ", md", ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne",
    ", nv", ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
    ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut", ", vt",
    ", va", ", wa", ", wv", ", wi", ", wy",
    "united states", "usa", ", us",
]

NON_DACH_CITIES = [
    "new york", "san francisco", "los angeles", "chicago", "boston",
    "seattle", "denver", "austin", "miami", "houston", "dallas",
    "atlanta", "phoenix", "portland", "philadelphia", "detroit",
    "london", "paris", "madrid", "barcelona", "rome", "milan",
    "lisbon", "warsaw", "prague", "budapest", "bucharest",
    "bangalore", "mumbai", "delhi", "singapore", "shanghai", "beijing",
    "tokyo", "sydney", "melbourne", "toronto", "vancouver", "montreal",
    "são paulo", "dubai", "abu dhabi",
]


def _is_wrong_location(job: RawJob) -> bool:
    """Filter out jobs that are clearly outside DACH region."""
    location_lower = (job.location or "").lower()
    if not location_lower:
        return False
    if any(us in location_lower for us in US_LOCATIONS):
        return True
    if any(city in location_lower for city in NON_DACH_CITIES):
        return True
    return False


def _is_negative(job: RawJob) -> bool:
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"
    if any(kw in title_lower for kw in NEGATIVE_KEYWORDS):
        return True
    if any(phrase in text for phrase in EXCLUSION_PHRASES):
        return True
    if any(phrase in text for phrase in GERMAN_C1_REQUIRED):
        return True
    return False
