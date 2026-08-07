from __future__ import annotations

import json
import logging
import re
from typing import Any
import aiohttp
from bs4 import BeautifulSoup

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

BUILTIN_COUNTRY_MAP = {
    "de": "DEU",
    "at": "AUT",
    "ch": "CHE",
    "nl": "NLD",
    "be": "BEL",
    "br": "BRA",
    "us": "USA",
    "uk": "GBR",
    "gb": "GBR",
    "es": "ESP",
    "it": "ITA",
    "fr": "FRA",
}


class BuiltInSource(JobSource):
    @property
    def source_name(self) -> str:
        return "builtin"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            for country in params.countries:
                country_code = BUILTIN_COUNTRY_MAP.get(country.lower(), "DEU")
                queries = params.queries_for_country(country)
                # Pick top queries to prevent excessive web calls
                for query in queries[:4]:
                    jobs = await self._fetch_query(session, query, country, country_code)
                    for j in jobs:
                        if j.external_id not in seen:
                            seen.add(j.external_id)
                            results.append(j)

        logger.info("BuiltIn: fetched %d unique jobs across %d countries", len(results), len(params.countries))
        return results

    async def _fetch_query(
        self,
        session: aiohttp.ClientSession,
        query: str,
        country: str,
        country_code: str,
    ) -> list[RawJob]:
        url = "https://builtin.com/jobs"
        req_params = {"search": query, "country": country_code}

        try:
            async with session.get(url, params=req_params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("BuiltIn returned status %s for query=%s country=%s", resp.status, query, country)
                    return []
                html_text = await resp.text()
        except Exception as e:
            logger.warning("BuiltIn fetch error for query=%s country=%s: %s", query, country, e)
            return []

        soup = BeautifulSoup(html_text, "html.parser")
        jobs: list[RawJob] = []

        # 1. Parse JSON-LD script tag for structured details
        json_ld_scripts = soup.find_all("script", type=re.compile(r"ld\+json"))
        items_map: dict[str, dict[str, Any]] = {}

        for script in json_ld_scripts:
            try:
                data = json.loads(script.string or "")
                graph = data.get("@graph", [data]) if isinstance(data, dict) else []
                for item in graph:
                    if item.get("@type") == "ItemList":
                        elements = item.get("itemListElement", [])
                        for el in elements:
                            u = el.get("url")
                            if u:
                                items_map[u] = el
            except Exception:
                continue

        # 2. Parse job cards
        cards = soup.find_all("div", attrs={"data-id": "job-card"})
        for card in cards:
            try:
                title_elem = card.find("a", attrs={"data-id": "job-card-title"})
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                rel_url = title_elem.get("href", "")
                if rel_url.startswith("/"):
                    full_url = f"https://builtin.com{rel_url}"
                else:
                    full_url = rel_url

                company_elem = card.find("a", attrs={"data-id": "company-title"})
                company_name = company_elem.get_text(strip=True) if company_elem else None

                card_text = card.get_text(separator=" ", strip=True)
                is_remote: bool | None = None
                if "Remote" in card_text or "In-Office or Remote" in card_text or "Hybrid" in card_text:
                    is_remote = True
                elif "In-Office" in card_text:
                    is_remote = False

                location: str | None = None
                loc_elem = card.find("i", class_=re.compile(r"fa-location-dot"))
                if loc_elem and loc_elem.parent and loc_elem.parent.parent:
                    location = loc_elem.parent.parent.get_text(strip=True)

                ld_item = items_map.get(full_url) or items_map.get(rel_url)
                description = ld_item.get("description", "") if ld_item else ""

                if not title or not full_url:
                    continue

                job_id = card.get("id") or card.get("data-builtin-track-job-id")
                if not job_id:
                    job_id = f"builtin_{re.sub(r'[^a-z0-9]', '_', full_url.lower())[-50:]}"
                else:
                    job_id = f"builtin_{job_id}"

                jobs.append(
                    RawJob(
                        external_id=str(job_id),
                        source="builtin",
                        title=title,
                        company_name=company_name,
                        location=location or country.upper(),
                        country=country.upper(),
                        description=description,
                        url=full_url,
                        is_remote=is_remote,
                        raw_data={"query": query, "country": country},
                    )
                )
            except Exception as e:
                logger.debug("BuiltIn card parse error: %s", e)
                continue

        return jobs
