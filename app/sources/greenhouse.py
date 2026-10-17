"""Greenhouse public job-board API for whitelisted employers.

Used by whitelist markets (South Korea): open boards are almost entirely
Korean-language there, so vacancies come straight from employers' own ATS.
The board list (~0.5 MB for Coupang) carries no descriptions; full content
is fetched only for in-market postings whose title names the target
function, instead of downloading every description (~12 MB) each hour.

API: https://developers.greenhouse.io/job-board.html (no authentication).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

import aiohttp
from dateutil import parser as dateparser

from app.markets import korea
from app.sources.base import RawJob, SearchParams, normalise_posted_at

logger = logging.getLogger(__name__)

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
REQUEST_TIMEOUT = 30
DETAIL_CONCURRENCY = 4
MAX_DETAILS_PER_BOARD = 120

# market code -> (employer registry, location markers)
MARKETS = {
    korea.MARKET_CODE: (korea.EMPLOYERS, korea.LOCATION_MARKERS),
}

# Function words Korean ATS titles use beyond the generic domain list.
_EXTRA_TITLE_TERMS = ("scm", "instock", "in-stock", "s&op")


def _html_to_text(value: str | None) -> str:
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(r"(?i)<\s*(?:br|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _parse_date(value):
    if not value:
        return None
    try:
        return normalise_posted_at(dateparser.parse(str(value)))
    except (TypeError, ValueError, OverflowError):
        return None


class GreenhouseSource:
    @property
    def source_name(self) -> str:
        return "greenhouse"

    async def search(self, params: SearchParams) -> list[RawJob]:
        requested = {country.casefold() for country in params.countries}
        boards = [
            (market, employer, markers)
            for market, (employers, markers) in MARKETS.items()
            if market in requested
            for employer in employers
            if employer.ats and employer.ats[0] == "greenhouse"
        ]
        if not boards:
            return []

        # Lazy import: app.scoring.rules imports app.sources at module load.
        from app.scoring.rules import TITLE_DOMAIN_KEYWORDS  # noqa: PLC0415
        title_terms = (*TITLE_DOMAIN_KEYWORDS, *_EXTRA_TITLE_TERMS)

        results: list[RawJob] = []
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            for market, employer, markers in boards:
                try:
                    jobs = await self._board(http, market, employer, markers, title_terms)
                except Exception as exc:  # one board must not sink the scan
                    logger.warning("Greenhouse %s failed: %s", employer.ats[1], exc)
                    continue
                results.extend(jobs)
        logger.info("Greenhouse: %d in-scope jobs from %d boards", len(results), len(boards))
        return results

    async def _board(self, http, market, employer, markers, title_terms) -> list[RawJob]:
        token = employer.ats[1]
        async with http.get(f"{API_BASE}/{token}/jobs") as response:
            if response.status != 200:
                logger.warning("Greenhouse %s list returned HTTP %d", token, response.status)
                return []
            listing = (await response.json(content_type=None)).get("jobs", [])

        in_scope = [
            item for item in listing
            if any(m in ((item.get("location") or {}).get("name") or "").casefold() for m in markers)
            and any(term in (item.get("title") or "").casefold() for term in title_terms)
        ][:MAX_DETAILS_PER_BOARD]

        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def detail(item):
            async with semaphore:
                async with http.get(f"{API_BASE}/{token}/jobs/{item['id']}") as response:
                    if response.status != 200:
                        return None
                    return await response.json(content_type=None)

        details = await asyncio.gather(*(detail(item) for item in in_scope), return_exceptions=True)
        jobs: list[RawJob] = []
        for item in details:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            jobs.append(
                RawJob(
                    external_id=f"greenhouse_{token}_{item['id']}",
                    source="greenhouse",
                    title=str(item.get("title") or "").strip(),
                    company_name=employer.name,
                    location=(item.get("location") or {}).get("name"),
                    country=market.upper(),
                    description=_html_to_text(item.get("content")) or None,
                    url=item.get("absolute_url"),
                    posted_at=_parse_date(item.get("first_published") or item.get("updated_at")),
                    raw_data={
                        "board": token,
                        "employer_group": employer.group,
                        "departments": [d.get("name") for d in item.get("departments") or []],
                    },
                )
            )
        return jobs
