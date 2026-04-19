"""Jooble job aggregator source.

API docs: https://jooble.org/api/about
Endpoint: POST https://jooble.org/api/{api_key}

Note: Jooble returns only a short snippet (~150 chars), not full descriptions.
Jobs will show a 🟡 data-quality indicator in the dashboard.
Links are Jooble redirect URLs — they forward to the original posting.

Jooble is a meta-aggregator — it covers sources we can't reach directly
(Stepstone, Monster, regional boards) which makes it valuable despite
the snippet limitation.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import aiohttp

from app.config import settings
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

JOOBLE_API_URL = "https://jooble.org/api/{api_key}"

# Countries we search + their Jooble location strings
COUNTRY_LOCATIONS: dict[str, tuple[str, str]] = {
    "de": ("Germany", "DE"),
    "at": ("Austria", "AT"),
    "ch": ("Switzerland", "CH"),
    "nl": ("Netherlands", "NL"),
    "be": ("Belgium", "BE"),
    "pl": ("Poland", "PL"),
    "cz": ("Czech Republic", "CZ"),
}

# Key queries sent to Jooble — representative sample, not all user queries
# (Jooble is slow-ish, keep to ~8 queries × 3 countries × 2 pages = 48 requests max)
JOOBLE_QUERIES = [
    "Director Supply Chain",
    "Head of Procurement",
    "VP Operations",
    "Head of Logistics",
    "Chief Operating Officer",
    "Director Operations",
    "Head of Sourcing",
    "Supply Chain Director",
]


def _parse_salary(raw: str) -> tuple[float | None, float | None]:
    """Parse Jooble salary string '€70,000 – €85,000' → (70000.0, 85000.0)."""
    if not raw:
        return None, None
    cleaned = raw.replace(",", "").replace(".", "")
    nums = [float(n) for n in re.findall(r"\d{4,}", cleaned)]
    if not nums:
        return None, None
    return nums[0], nums[-1] if len(nums) > 1 else nums[0]


class JoobleSource(JobSource):
    @property
    def source_name(self) -> str:
        return "jooble"

    _auth_failed: bool = False  # class-level flag — stop retrying on 403

    # Budget control: free tier = 500 requests total (not per day).
    # We run 8 queries × 1 country × 1 page = 8 requests per scan (every 3 h).
    # At that rate 500 req ≈ 62 scans ≈ ~8 days before needing a new key.
    # Override via JOOBLE_REQUESTS_PER_SCAN in .env if you have a higher-limit key.
    REQUESTS_PER_SCAN: int = 8   # = len(JOOBLE_QUERIES) × 1 country × 1 page

    async def search(self, params: SearchParams) -> list[RawJob]:
        if not settings.jooble_api_key:
            logger.debug("Jooble: no API key configured, skipping")
            return []
        if JoobleSource._auth_failed:
            logger.warning("Jooble: skipping — previous 403, check JOOBLE_API_KEY in .env")
            return []

        api_url = JOOBLE_API_URL.format(api_key=settings.jooble_api_key)

        # Primary country only (DE or first preferred DACH country) — 1 request per query
        dach_preferred = [c for c in params.countries if c in COUNTRY_LOCATIONS]
        primary_country = dach_preferred[0] if dach_preferred else "de"
        location_str, country_upper = COUNTRY_LOCATIONS[primary_country]

        results: list[RawJob] = []
        seen: set[str] = set()
        request_count = 0

        async with aiohttp.ClientSession() as http:
            for query in JOOBLE_QUERIES:  # 8 queries × 1 page = 8 requests/scan
                batch = await self._fetch(
                    http, api_url, query, location_str, country_upper, page=1
                )
                request_count += 1
                for job in batch:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)
                if JoobleSource._auth_failed:
                    break  # stop all queries on first 403

        logger.info(
            "Jooble: %d jobs fetched (%d API requests, budget ~500 total)",
            len(results), request_count,
        )
        # Expose for aggregator stats
        self._last_request_count = request_count
        return results

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        keywords: str,
        location: str,
        country: str,
        page: int,
    ) -> list[RawJob]:
        payload = {
            "keywords": keywords,
            "location": location,
            "page": page,
            "ResultOnPage": 20,
        }
        try:
            async with session.post(
                api_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 403:
                    logger.warning("Jooble: 403 Forbidden — invalid API key, disabling until restart")
                    JoobleSource._auth_failed = True
                    return []
                if resp.status != 200:
                    logger.warning(
                        "Jooble: HTTP %d for query=%r location=%r page=%d",
                        resp.status, keywords, location, page,
                    )
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("Jooble request failed (query=%r): %s", keywords, exc)
            return []

        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            try:
                job_id = str(item.get("id", "")).strip()
                title = str(item.get("title", "")).strip()
                if not title or not job_id:
                    continue

                company = str(item.get("company", "")).strip() or None
                snippet = str(item.get("snippet", "")).strip()
                link = str(item.get("link", "")).strip()
                job_location = str(item.get("location", location)).strip()
                salary_str = str(item.get("salary", "")).strip()
                updated = item.get("updated", "")

                posted_at: datetime | None = None
                if updated:
                    try:
                        posted_at = datetime.fromisoformat(
                            str(updated).replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

                sal_min, sal_max = _parse_salary(salary_str)

                jobs.append(
                    RawJob(
                        external_id=f"jooble_{job_id}",
                        source="jooble",
                        title=title,
                        company_name=company,
                        location=job_location,
                        country=country,
                        description=snippet,   # snippet only — data_quality=partial
                        salary_min=sal_min,
                        salary_max=sal_max,
                        salary_currency="EUR",
                        url=link,
                        is_remote=None,
                        posted_at=posted_at,
                        raw_data={
                            "jooble_id": job_id,
                            "salary_raw": salary_str,
                            "source_board": item.get("source", ""),
                            "job_type": item.get("type", ""),
                        },
                    )
                )
            except Exception as exc:
                logger.debug("Jooble parse error: %s", exc)

        return jobs
