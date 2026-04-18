"""Welcome to the Jungle (formerly Otta) source.

Uses the public Algolia search API embedded in WTTJ's frontend.
App ID and search key are public client-side credentials — no auth required.

URL pattern: https://www.welcometothejungle.com/en/companies/{org_slug}/jobs/{slug}
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import aiohttp

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

ALGOLIA_APP_ID = "CSEKHVMS53"
ALGOLIA_SEARCH_KEY = "4bd8f6215d0cc52b26430765769e65a0"
ALGOLIA_INDEX = "wttj_jobs_production_en"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"

BASE_URL = "https://www.welcometothejungle.com"

# Country code mapping: SearchParams uses lowercase, Algolia uses uppercase 2-letter
_COUNTRY_MAP = {
    "de": "DE", "at": "AT", "nl": "NL", "ch": "CH", "be": "BE",
    "fr": "FR", "gb": "GB", "pl": "PL", "cz": "CZ", "sk": "SK",
    "ro": "RO", "hu": "HU", "si": "SI", "es": "ES", "pt": "PT",
    "it": "IT", "se": "SE", "dk": "DK", "no": "NO", "fi": "FI",
    "lu": "LU", "ie": "IE",
}


def _build_description(hit: dict) -> str:
    """Combine summary + key_missions + profile into a single text block."""
    parts: list[str] = []

    summary = hit.get("summary") or ""
    if summary:
        parts.append(summary.strip())

    missions = hit.get("key_missions") or []
    if isinstance(missions, list) and missions:
        parts.append("Key missions:\n" + "\n".join(f"• {m}" for m in missions if m))
    elif isinstance(missions, str) and missions.strip():
        parts.append(missions.strip())

    profile = hit.get("profile") or ""
    if profile and isinstance(profile, str):
        # Strip markdown headings for cleaner text
        profile = re.sub(r"#{1,6}\s*", "", profile)
        parts.append(profile.strip())

    return "\n\n".join(parts)[:4000]


def _job_url(hit: dict) -> str:
    org = hit.get("organization") or {}
    org_slug = org.get("slug", "") if isinstance(org, dict) else ""
    job_slug = hit.get("slug", "")
    if org_slug and job_slug:
        return f"{BASE_URL}/en/companies/{org_slug}/jobs/{job_slug}"
    return f"{BASE_URL}/en/jobs"


def _location(hit: dict) -> tuple[str | None, str | None]:
    """Return (location_str, country_code) from offices list."""
    offices = hit.get("offices") or []
    if not offices:
        return None, None
    # Prefer first office
    office = offices[0]
    city = office.get("city") or ""
    country = office.get("country_code") or ""
    location = ", ".join(filter(None, [city, country]))
    return location or None, country.lower() or None


class WTTJSource(JobSource):
    """Welcome to the Jungle (formerly Otta) — Algolia-based job search."""

    @property
    def source_name(self) -> str:
        return "wttj"

    async def search(self, params: SearchParams) -> list[RawJob]:
        # Build country facet filters (OR logic inside inner list)
        country_codes = [
            _COUNTRY_MAP[c.lower()]
            for c in params.countries
            if c.lower() in _COUNTRY_MAP
        ]
        if not country_codes:
            country_codes = ["DE"]  # default to Germany

        country_facets = [f"offices.country_code:{cc}" for cc in country_codes]

        headers = {
            "x-algolia-application-id": ALGOLIA_APP_ID,
            "x-algolia-api-key": ALGOLIA_SEARCH_KEY,
            "content-type": "application/json",
            "origin": BASE_URL,
            "referer": BASE_URL + "/",
        }

        results: list[RawJob] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession(headers=headers) as session:
            for query in params.queries:
                jobs = await self._search_query(session, query, country_facets)
                for job in jobs:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)

        logger.info("WTTJ: %d jobs fetched", len(results))
        return results

    async def _search_query(
        self,
        session: aiohttp.ClientSession,
        query: str,
        country_facets: list[str],
    ) -> list[RawJob]:
        payload = {
            "requests": [
                {
                    "indexName": ALGOLIA_INDEX,
                    "query": query,
                    "hitsPerPage": params_per_query := 50,
                    "page": 0,
                    "facetFilters": [country_facets],  # outer list = AND, inner = OR
                    "filters": "contract_type:full_time",
                }
            ]
        }

        try:
            async with session.post(
                ALGOLIA_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.warning("WTTJ Algolia returned %s for query '%s'", resp.status, query)
                    return []
                data = await resp.json()
        except Exception as exc:
            logger.error("WTTJ fetch failed for query '%s': %s", query, exc)
            return []

        hits = data.get("results", [{}])[0].get("hits", [])
        jobs: list[RawJob] = []

        for hit in hits:
            try:
                title = hit.get("name", "").strip()
                if not title:
                    continue

                org = hit.get("organization") or {}
                company = org.get("name", "").strip() if isinstance(org, dict) else ""

                location, country = _location(hit)
                description = _build_description(hit)
                url = _job_url(hit)

                # Parse published_at
                posted_at: datetime | None = None
                pub_str = hit.get("published_at") or ""
                if pub_str:
                    try:
                        posted_at = datetime.fromisoformat(
                            pub_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        pass

                # Remote
                remote_val = hit.get("remote")
                is_remote: bool | None = None
                if remote_val == "full":
                    is_remote = True
                elif remote_val == "no":
                    is_remote = False
                elif remote_val == "partial":
                    is_remote = None  # hybrid — leave as None

                # Salary
                salary_min = hit.get("salary_minimum") or hit.get("salary_yearly_minimum")
                salary_max = hit.get("salary_maximum")
                salary_currency = hit.get("salary_currency")
                # Convert to float if present
                try:
                    salary_min = float(salary_min) if salary_min else None
                    salary_max = float(salary_max) if salary_max else None
                except (TypeError, ValueError):
                    salary_min = salary_max = None

                ext_id = f"wttj_{hit.get('objectID', '')}"

                jobs.append(
                    RawJob(
                        external_id=ext_id,
                        source="wttj",
                        title=title,
                        company_name=company or None,
                        location=location,
                        country=country,
                        description=description,
                        url=url,
                        is_remote=is_remote,
                        posted_at=posted_at,
                        salary_min=salary_min,
                        salary_max=salary_max,
                        salary_currency=salary_currency,
                        raw_data={
                            "algolia_object_id": hit.get("objectID"),
                            "contract_type": hit.get("contract_type"),
                            "remote": remote_val,
                            "query": query,
                        },
                    )
                )
            except Exception as exc:
                logger.debug("WTTJ hit parse error: %s", exc)

        return jobs
