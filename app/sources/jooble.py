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

    async def search(self, params: SearchParams) -> list[RawJob]:
        if not settings.jooble_api_key:
            logger.debug("Jooble: no API key configured, skipping")
            return []

        api_url = JOOBLE_API_URL.format(api_key=settings.jooble_api_key)

        # Intersect user's preferred countries with Jooble-supported ones
        target_countries = [c for c in params.countries if c in COUNTRY_LOCATIONS]
        if not target_countries:
            target_countries = ["de"]

        # Use JOOBLE_QUERIES; fall back to first N user queries if profile has custom ones
        queries = JOOBLE_QUERIES
        if params.queries:
            # Include any user queries that aren't covered by JOOBLE_QUERIES
            extra = [q for q in params.queries if q not in JOOBLE_QUERIES][:4]
            queries = JOOBLE_QUERIES + extra

        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as http:
            for query in queries:
                for country_code in target_countries[:3]:  # max 3 countries per query
                    location_str, country_upper = COUNTRY_LOCATIONS[country_code]
                    for page in range(1, 3):  # max 2 pages per query/country
                        batch = await self._fetch(
                            http, api_url, query, location_str, country_upper, page
                        )
                        for job in batch:
                            if job.external_id not in seen:
                                seen.add(job.external_id)
                                results.append(job)
                        if not batch:
                            break  # no more pages

        logger.info("Jooble: %d jobs fetched", len(results))
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
                    logger.warning("Jooble: 403 Forbidden — invalid API key?")
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
