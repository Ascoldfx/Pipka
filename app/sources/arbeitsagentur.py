from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from dateutil import parser as dateparser

from app.config import settings
from app.sources.base import RawJob, SearchParams

logger = logging.getLogger(__name__)

BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"


class ArbeitsagenturSource:
    @property
    def source_name(self) -> str:
        return "arbeitsagentur"

    async def search(self, params: SearchParams) -> list[RawJob]:
        if "de" not in params.countries:
            return []

        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for query in params.queries:
                for location in params.locations or [""]:
                    jobs = await self._fetch(session, query, location, params.results_per_query)
                    for job in jobs:
                        if job.external_id not in seen:
                            seen.add(job.external_id)
                            results.append(job)
        return results

    async def _fetch(
        self, session: aiohttp.ClientSession, query: str, location: str, limit: int
    ) -> list[RawJob]:
        headers = {"X-API-Key": settings.arbeitsagentur_api_key}
        request_params: dict = {
            "was": query,
            "size": min(limit, 50),
            "page": 1,
        }
        if location:
            request_params["wo"] = location

        try:
            async with session.get(BASE_URL, params=request_params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("Arbeitsagentur returned %s for '%s'", resp.status, query)
                    return []
                data = await resp.json()
        except Exception as e:
            logger.error("Arbeitsagentur request failed: %s", e)
            return []

        jobs: list[RawJob] = []
        for item in data.get("stellenangebote", []):
            try:
                ref_nr = item.get("refnr", "")
                title = item.get("titel", "")
                company = item.get("arbeitgeber", "")
                ort = item.get("arbeitsort", {})
                location_str = ort.get("ort", "") if isinstance(ort, dict) else str(ort)
                plz = ort.get("plz", "") if isinstance(ort, dict) else ""

                posted = None
                if item.get("eintrittsdatum"):
                    try:
                        posted = dateparser.parse(item["eintrittsdatum"]).replace(tzinfo=None)
                    except Exception:
                        pass
                if not posted and item.get("aktuelleVeroeffentlichungsdatum"):
                    try:
                        posted = dateparser.parse(item["aktuelleVeroeffentlichungsdatum"]).replace(tzinfo=None)
                    except Exception:
                        pass

                is_remote = None
                arbeitszeit = item.get("arbeitszeitmodelle", [])
                if isinstance(arbeitszeit, list) and "HOME_OFFICE" in arbeitszeit:
                    is_remote = True

                loc_display = f"{location_str} {plz}".strip() if location_str else None
                detail_url = f"https://www.arbeitsagentur.de/jobsuche/suche?id={ref_nr}" if ref_nr else None

                jobs.append(
                    RawJob(
                        external_id=f"ba_{ref_nr}",
                        source="arbeitsagentur",
                        title=title,
                        company_name=company or None,
                        location=loc_display,
                        country="DE",
                        description=item.get("beruf", ""),
                        salary_min=None,
                        salary_max=None,
                        salary_currency="EUR",
                        url=detail_url,
                        is_remote=is_remote,
                        posted_at=posted,
                        raw_data=item,
                    )
                )
            except Exception as e:
                logger.debug("Arbeitsagentur parse error: %s", e)
        return jobs
