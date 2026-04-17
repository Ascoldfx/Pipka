"""Xing job scraper — parses Apollo state from SSR HTML.

No login, no proxy, no Apify required.
Xing embeds full job search results in window.crate.serverData.APOLLO_STATE
as a JSON object. We parse it directly to extract structured job data.

Limit: ~20 results per search query (Xing caps unauth users at first page).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

import aiohttp

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

XING_SEARCH_URL = "https://www.xing.com/jobs/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}


class XingSource(JobSource):
    """Scrapes Xing job search by parsing Apollo SSR state from page HTML."""

    @property
    def source_name(self) -> str:
        return "xing"

    async def search(self, params: SearchParams) -> list[RawJob]:
        # Xing is primarily DACH — only search if DE/AT/CH/BE/NL in target
        xing_countries = {"de", "at", "ch", "be", "nl", "pl", "cz", "gb", "fr"}
        if not any(c in xing_countries for c in params.countries):
            return []

        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [
                self._search_query(session, query, params)
                for query in params.queries
            ]
            all_results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, res in enumerate(all_results):
                if isinstance(res, Exception):
                    logger.warning("Xing query '%s' failed: %s", params.queries[i], res)
                    continue
                for job in res:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)

        logger.info("Xing: collected %d unique jobs", len(results))
        return results

    async def _search_query(
        self, session: aiohttp.ClientSession, query: str, params: SearchParams
    ) -> list[RawJob]:
        search_params = {
            "keywords": query,
            "location": "Deutschland",
            "radius": "50",
        }
        try:
            async with session.get(
                XING_SEARCH_URL,
                params=search_params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Xing returned %s for '%s'", resp.status, query)
                    return []
                html = await resp.text()
        except Exception as e:
            logger.error("Xing request failed for '%s': %s", query, e)
            return []

        return self._parse_jobs(html, query)

    def _parse_jobs(self, html: str, query: str) -> list[RawJob]:
        """Extract VisibleJob entries from Apollo state embedded in SSR HTML."""
        # Apollo state lives inside window.crate={...} script block
        m = re.search(r"window\.crate\s*=\s*(\{.+?)\s*</script>", html, re.DOTALL)
        if not m:
            logger.debug("Xing: no window.crate found in HTML")
            return []

        crate_raw = m.group(1).rstrip(";")
        try:
            crate = json.loads(crate_raw)
        except json.JSONDecodeError:
            # Sometimes the JSON has trailing comma or other issues — try to find APOLLO_STATE directly
            logger.debug("Xing: window.crate JSON parse failed, trying APOLLO_STATE regex")
            return self._parse_via_regex(html, query)

        apollo = crate.get("serverData", {}).get("APOLLO_STATE", {})
        if not apollo:
            logger.debug("Xing: no APOLLO_STATE in crate")
            return self._parse_via_regex(html, query)

        return self._extract_from_apollo(apollo, query)

    def _extract_from_apollo(self, apollo: dict, query: str) -> list[RawJob]:
        """Parse VisibleJob entries from Apollo state dict."""
        jobs = []
        for key, val in apollo.items():
            if not key.startswith("VisibleJob:"):
                continue
            if not isinstance(val, dict):
                continue

            try:
                job = self._build_job(key, val, query)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.debug("Xing: failed to parse job %s: %s", key, e)

        return jobs

    def _build_job(self, key: str, val: dict, query: str) -> RawJob | None:
        job_id = val.get("id", key.split(":")[-1])
        title = val.get("title", "")
        url = val.get("url", "").replace("\\u002F", "/")
        if not url:
            slug = val.get("slug", "")
            url = f"https://www.xing.com/jobs/{slug}" if slug else ""

        if not title or not url:
            return None

        # Location
        location_obj = val.get("location") or {}
        city = location_obj.get("city", "") if isinstance(location_obj, dict) else ""

        # Company name from companyInfo
        company_name = None
        company_info = val.get("companyInfo") or {}
        if isinstance(company_info, dict):
            company_name = company_info.get("companyNameOverride") or None
            # Also try via __ref resolution — not available without full state
            if not company_name:
                company_ref = (company_info.get("company") or {})
                # company_ref is usually {"__ref": "Company:xxx"} — name not embedded
                pass

        # Salary
        salary_obj = val.get("salary") or {}
        salary_min = salary_obj.get("minimum") if isinstance(salary_obj, dict) else None
        salary_max = salary_obj.get("maximum") if isinstance(salary_obj, dict) else None
        salary_currency = salary_obj.get("currency", "EUR") if isinstance(salary_obj, dict) else "EUR"

        # Date
        posted_at = None
        for date_field in ("refreshedAt", "activatedAt", "activeUntil"):
            raw_date = val.get(date_field)
            if raw_date:
                try:
                    posted_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).replace(tzinfo=None)
                    if date_field != "activeUntil":
                        break
                except (ValueError, AttributeError):
                    pass

        # Remote
        is_remote = None
        remote_info = val.get("remoteOption") or {}
        if isinstance(remote_info, dict):
            remote_val = remote_info.get("localizationValue", "")
            if "Home-Office" in remote_val or "Remote" in remote_val:
                is_remote = True

        return RawJob(
            external_id=f"xing_{job_id}",
            source="xing",
            title=title,
            company_name=company_name,
            location=city or None,
            country="DE",
            description="",  # Full description requires auth; title+company used for scoring
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            salary_currency=salary_currency,
            url=url,
            is_remote=is_remote,
            posted_at=posted_at,
            raw_data={"query": query, "xing_id": job_id},
        )

    def _parse_via_regex(self, html: str, query: str) -> list[RawJob]:
        """Fallback: extract VisibleJob entries directly from raw HTML via regex."""
        pattern = re.compile(
            r'"(VisibleJob:[^"]+)":\{(.*?)(?="(?:VisibleJob|Company|ROOT_QUERY|Viewer):)',
            re.DOTALL,
        )
        jobs = []
        for m in pattern.finditer(html):
            key = m.group(1)
            val_str = m.group(2)

            def ex(p: str, default: str = "") -> str:
                hit = re.search(p, val_str)
                return hit.group(1) if hit else default

            title = ex(r'"title":"([^"]+)"')
            if not title:
                continue
            try:
                title = title.encode("raw_unicode_escape").decode("unicode_escape")
            except Exception:
                pass

            url = ex(r'"url":"([^"]+)"').replace("\\u002F", "/")
            if not url:
                slug = ex(r'"slug":"([^"]+)"')
                url = f"https://www.xing.com/jobs/{slug}" if slug else ""

            city = ex(r'"city":"([^"]+)"')
            company = ex(r'"companyNameOverride":"([^"]+)"') or None
            job_id = ex(r'"id":"([^"]+)"') or key.split(":")[-1]
            refreshed = ex(r'"refreshedAt":"([^"]+)"')

            salary_min_s = ex(r'"minimum":(\d+)')
            salary_max_s = ex(r'"maximum":(\d+)')

            posted_at = None
            if refreshed:
                try:
                    posted_at = datetime.fromisoformat(refreshed.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass

            try:
                jobs.append(RawJob(
                    external_id=f"xing_{job_id}",
                    source="xing",
                    title=title,
                    company_name=company,
                    location=city or None,
                    country="DE",
                    description="",
                    salary_min=float(salary_min_s) if salary_min_s else None,
                    salary_max=float(salary_max_s) if salary_max_s else None,
                    salary_currency="EUR",
                    url=url,
                    is_remote=None,
                    posted_at=posted_at,
                    raw_data={"query": query, "xing_id": job_id},
                ))
            except Exception as e:
                logger.debug("Xing regex parse error for %s: %s", key, e)

        return jobs
