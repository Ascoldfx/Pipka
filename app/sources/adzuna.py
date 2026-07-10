from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
from dateutil import parser as dateparser

from app.config import settings
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

# Country endpoints Adzuna actually operates (their API 404s on anything else).
# Notably ABSENT: ae, id — Gulf/Indonesia coverage comes from Indeed/Jooble.
ADZUNA_SUPPORTED = {
    "gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr",
    "in", "it", "mx", "nl", "nz", "pl", "sg", "za",
}

# Adzuna free tier rate-limits aggressively (≈25 hits/min). The scan can ask
# for 30 queries × 13 countries — a fully sequential 1000+ request crawl that
# blows past the aggregator's 120s timeout AND the daily quota. We cap the
# Cartesian product and run requests with bounded concurrency + pacing.
ADZUNA_MAX_COMBOS = 80        # query × country pairs per scan (most-relevant first)
ADZUNA_MAX_PAGES = 2          # pages per combo (was 3)
ADZUNA_CONCURRENCY = 6        # parallel in-flight requests
ADZUNA_PACE_SECONDS = 0.4     # min gap between request starts (≈150/min ceiling)
ADZUNA_REQUEST_TIMEOUT = 8    # per-page HTTP timeout (was 15) — fail fast on Adzuna 500s/slow pages


class AdzunaSource:
    @property
    def source_name(self) -> str:
        return "adzuna"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        locations = params.locations or [""]
        # Build the (country, location, query) work list, capped so we don't
        # explode into a 1000-request sequential crawl. Order matters: query is
        # the OUTER loop so the cap keeps the top-priority titles covered across
        # ALL countries. (The old country-outer nesting burned the whole cap on
        # the first country's query list — 7 of 9 countries were never searched.)
        countries = [c for c in params.countries if c.lower() in ADZUNA_SUPPORTED]
        skipped = set(params.countries) - set(countries)
        if skipped:
            logger.debug("Adzuna: skipping unsupported countries %s", sorted(skipped))

        combos: list[tuple[str, str, str]] = []
        for query in params.queries:
            for country in countries:
                for location in locations:
                    combos.append((country, location, query))
        if len(combos) > ADZUNA_MAX_COMBOS:
            logger.info(
                "Adzuna: capping %d combos → %d (rate-limit / timeout guard)",
                len(combos), ADZUNA_MAX_COMBOS,
            )
            combos = combos[:ADZUNA_MAX_COMBOS]

        sem = asyncio.Semaphore(ADZUNA_CONCURRENCY)
        pace_lock = asyncio.Lock()
        last_start = {"t": 0.0}

        async def _paced_combo(session, country, location, query) -> list[RawJob]:
            """Fetch up to ADZUNA_MAX_PAGES for one combo, paced + bounded."""
            out: list[RawJob] = []
            async with sem:
                for page in range(1, ADZUNA_MAX_PAGES + 1):
                    # Global pacer so concurrent workers don't burst past the
                    # free-tier rate limit.
                    async with pace_lock:
                        import time as _t  # noqa: PLC0415
                        gap = _t.monotonic() - last_start["t"]
                        if gap < ADZUNA_PACE_SECONDS:
                            await asyncio.sleep(ADZUNA_PACE_SECONDS - gap)
                        last_start["t"] = _t.monotonic()
                    jobs = await self._fetch_page(
                        session, country, location, query,
                        min(params.results_per_query, 50), page,
                    )
                    out.extend(jobs)
                    if len(jobs) < 20:
                        break  # no more pages
            return out

        async with aiohttp.ClientSession() as session:
            tasks = [_paced_combo(session, c, loc, q) for (c, loc, q) in combos]
            for fut in asyncio.as_completed(tasks):
                jobs = await fut
                for job in jobs:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)
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
            async with session.get(url, params=request_params, timeout=aiohttp.ClientTimeout(total=ADZUNA_REQUEST_TIMEOUT)) as resp:
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
