from __future__ import annotations

import logging
from datetime import datetime
import urllib.parse

import aiohttp
from dateutil import parser as dateparser

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

REMOTIVE_BASE = "https://remotive.com/api/remote-jobs"

class RemotiveSource(JobSource):
    @property
    def source_name(self) -> str:
        return "remotive"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for query in params.queries:
                jobs = await self._fetch(session, query, params)
                for job in jobs:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)
        return results

    async def _fetch(
        self, session: aiohttp.ClientSession, query: str, params: SearchParams
    ) -> list[RawJob]:
        
        # Remotive has search by string. We should just pick the most important keywords.
        keywords = urllib.parse.quote(query)
        url = f"{REMOTIVE_BASE}?search={keywords}&limit={min(params.results_per_query, 50)}"
        
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Remotive query '%s' returned %s", query, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.error("Remotive request failed: %s", e)
            return []

        jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            try:
                candidate_required_location = item.get("candidate_required_location", "").lower()
                
                # Check location restriction if any. We accept global or specific EU countries.
                location_ok = False
                if not candidate_required_location or "worldwide" in candidate_required_location or "global" in candidate_required_location:
                    location_ok = True
                elif "europe" in candidate_required_location:
                    location_ok = True
                else:
                    # check if any allowed country matches
                    for c in params.countries:
                        if c in candidate_required_location:
                            location_ok = True
                            break
                            
                if not location_ok:
                    continue

                posted = dateparser.parse(item["publication_date"]).replace(tzinfo=None) if item.get("publication_date") else None
                salary = item.get("salary", "")
                
                jobs.append(
                    RawJob(
                        external_id=f"remotive_{item.get('id')}",
                        source="remotive",
                        title=str(item.get("title", "")),
                        company_name=str(item.get("company_name", "")),
                        location="Remote",
                        country="Remote",
                        description=str(item.get("description", "")),
                        salary_min=None, # It's a string in Remotive API
                        salary_max=None,
                        salary_currency="USD",
                        url=item.get("url", ""),
                        is_remote=True,
                        posted_at=posted,
                        raw_data=item,
                    )
                )
            except Exception as e:
                logger.debug("Remotive parse error: %s", e)
        return jobs
