"""WatchlistSource — scans job boards for vacancies at specific target companies.

For each company in the user's watchlist, queries Adzuna with the company name
filter + typical supply-chain titles. Results are tagged source="watchlist".
"""
from __future__ import annotations

import logging

import aiohttp
from dateutil import parser as dateparser

from app.config import settings
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

# Generic senior-level titles to search per company
WATCHLIST_TITLES = [
    "Supply Chain",
    "Procurement",
    "Operations",
    "Logistics",
    "Sourcing",
    "Director",
    "Head of",
    "VP",
]


class WatchlistSource:
    """Searches Adzuna for jobs at specific companies from the user's watchlist."""

    @property
    def source_name(self) -> str:
        return "watchlist"

    async def search(self, params: SearchParams) -> list[RawJob]:
        """params.queries is treated as the list of target company names."""
        companies = params.queries
        if not companies:
            return []

        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for company in companies:
                for country in params.countries:
                    jobs = await self._search_company(session, company, country)
                    for job in jobs:
                        if job.external_id not in seen:
                            seen.add(job.external_id)
                            results.append(job)

        logger.info("WatchlistSource: %d jobs across %d companies", len(results), len(companies))
        return results

    async def _search_company(
        self, session: aiohttp.ClientSession, company: str, country: str
    ) -> list[RawJob]:
        """Search Adzuna for this specific company in the given country."""
        url = f"{ADZUNA_BASE}/{country}/search/1"
        params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": 50,
            # Use title_only search for company name to avoid noise
            "company": company,
            "sort_by": "date",
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.debug("WatchlistSource Adzuna %s/%s → %d", company, country, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.debug("WatchlistSource Adzuna error %s/%s: %s", company, country, e)
            return []

        jobs = []
        for item in data.get("results", []):
            job = self._parse_adzuna(item, company)
            if job:
                jobs.append(job)
        return jobs

    def _parse_adzuna(self, item: dict, watchlist_company: str) -> RawJob | None:
        try:
            title = item.get("title", "").strip()
            if not title:
                return None

            job_id = str(item.get("id", ""))
            if not job_id:
                return None

            company_name = item.get("company", {}).get("display_name") or watchlist_company
            location = item.get("location", {}).get("display_name", "")
            country_code = item.get("location", {}).get("area", [""])[0] if item.get("location", {}).get("area") else ""

            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            description = item.get("description", "")
            url = item.get("redirect_url", "")

            posted_at = None
            if item.get("created"):
                try:
                    posted_at = dateparser.parse(item["created"])
                except Exception:
                    pass

            return RawJob(
                external_id=f"watchlist_adzuna_{job_id}",
                source="watchlist",
                title=title,
                company_name=company_name,
                location=location,
                country=country_code or None,
                description=description,
                salary_min=float(salary_min) if salary_min else None,
                salary_max=float(salary_max) if salary_max else None,
                salary_currency="EUR",
                url=url,
                is_remote=None,
                posted_at=posted_at,
                raw_data={"watchlist_company": watchlist_company},
            )
        except Exception as e:
            logger.debug("WatchlistSource parse error: %s", e)
            return None
