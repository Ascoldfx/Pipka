from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from dateutil import parser as dateparser

from app.config import settings
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"


class AdzunaSource:
    @property
    def source_name(self) -> str:
        return "adzuna"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for country in params.countries:
                for location in params.locations or [""]:
                    for query in params.queries:
                        # Fetch multiple pages (up to 3) for more results
                        for page in range(1, 4):
                            jobs = await self._fetch_page(
                                session, country, location, query,
                                min(params.results_per_query, 50), page,
                            )
                            for job in jobs:
                                if job.external_id not in seen:
                                    seen.add(job.external_id)
                                    results.append(job)
                            # Stop if we got fewer than requested (no more pages)
                            if len(jobs) < 20:
                                break
        return results

    async def _fetch_page(
        self, session: aiohttp.ClientSession, country: str, location: str,
        query: str, limit: int, page: int = 1,
    ) -> list[RawJob]:
        url = f"{ADZUNA_BASE}/{country}/search/{page}"
        request_params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": min(limit, 50),
            "what": query,
            "sort_by": "date",
        }
        if location:
            request_params["where"] = location

        try:
            async with session.get(url, params=request_params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Adzuna %s/%s p%d returned %s", country, query, page, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.error("Adzuna request failed: %s", e)
            return []

        jobs: list[RawJob] = []
        for item in data.get("results", []):
            try:
                posted = dateparser.parse(item["created"]).replace(tzinfo=None) if item.get("created") else None
                title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                area = item.get("location", {}).get("display_name", "")

                # Use direct job URL if available, fallback to redirect_url
                job_url = item.get("redirect_url", "")

                jobs.append(
                    RawJob(
                        external_id=f"adzuna_{country}_{item['id']}",
                        source="adzuna",
                        title=title,
                        company_name=item.get("company", {}).get("display_name"),
                        location=area,
                        country=country.upper(),
                        description=item.get("description", ""),
                        salary_min=item.get("salary_min"),
                        salary_max=item.get("salary_max"),
                        salary_currency="EUR" if country in ("de", "at", "nl", "be", "fr") else "CHF" if country == "ch" else "EUR",
                        url=job_url,
                        is_remote=None,
                        posted_at=posted,
                        raw_data=item,
                    )
                )
            except Exception as e:
                logger.debug("Adzuna parse error: %s", e)
        return jobs
