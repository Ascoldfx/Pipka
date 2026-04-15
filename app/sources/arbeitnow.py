from __future__ import annotations

import logging
from datetime import datetime

import aiohttp

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

ARBEITNOW_BASE = "https://www.arbeitnow.com/api/job-board-api"

class ArbeitnowSource(JobSource):
    @property
    def source_name(self) -> str:
        return "arbeitnow"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            # Arbeitnow does not support complex query params via free API directly on search.
            # We fetch recent pages and filter locally.
            for page in range(1, 4):
                jobs = await self._fetch_page(session, page, params)
                for job in jobs:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)
                if not jobs:
                    break
        return results

    async def _fetch_page(
        self, session: aiohttp.ClientSession, page: int, params: SearchParams
    ) -> list[RawJob]:
        url = f"{ARBEITNOW_BASE}?page={page}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Arbeitnow p%d returned %s", page, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.error("Arbeitnow request failed: %s", e)
            return []

        jobs: list[RawJob] = []
        for item in data.get("data", []):
            try:
                title = str(item.get("title", ""))
                desc = str(item.get("description", ""))
                location = str(item.get("location", ""))
                
                # Local filtering since API doesn't support query parameters
                # Only keep if either title or description roughly matches queries
                title_lower = title.lower()
                desc_lower = desc.lower()
                
                query_matched = False
                for query in params.queries:
                    # Very loose match for Arbeitnow
                    words = query.lower().split()
                    if any(w in title_lower or w in desc_lower for w in words if len(w) > 4):
                        query_matched = True
                        break
                
                if not query_matched and params.queries:
                    continue
                    
                posted_timestamp = item.get("created_at")
                posted = datetime.fromtimestamp(posted_timestamp) if posted_timestamp else None

                jobs.append(
                    RawJob(
                        external_id=f"arbeitnow_{item.get('slug')}",
                        source="arbeitnow",
                        title=title,
                        company_name=item.get("company_name", ""),
                        location=location,
                        country="DE", # Arbeitnow is mostly Germany/Europe
                        description=desc,
                        salary_min=None,
                        salary_max=None,
                        salary_currency="EUR",
                        url=item.get("url", ""),
                        is_remote=item.get("remote"),
                        posted_at=posted,
                        raw_data=item,
                    )
                )
            except Exception as e:
                logger.debug("Arbeitnow parse error: %s", e)
        return jobs
