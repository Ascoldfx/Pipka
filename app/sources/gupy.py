"""Official Gupy job-board partner feed.

Gupy provides approved job-board partners with a periodically-polled JSON or
XML feed URL and an optional Authorization token.  Pipka intentionally does
not scrape the candidate portal: this adapter stays disabled until official
partner credentials are configured.

Docs:
https://developers.gupy.io/docs/integra%C3%A7%C3%A3o-com-job-boards-parceiros
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata

import aiohttp
from dateutil import parser as dateparser

from app.config import settings
from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

GUPY_REQUEST_TIMEOUT = 30
GUPY_MAX_JOBS_PER_SCAN = 2_000

_SENIOR_TITLE_TERMS = {
    "chief",
    "director",
    "diretor",
    "diretora",
    "gerente executivo",
    "gerente executiva",
    "gerente nacional",
    "head",
    "superintendente",
    "vice president",
    "vice-presidente",
}
_DOMAIN_TITLE_TERMS = {
    "abastecimento",
    "cadeia de suprimentos",
    "compras",
    "crise",
    "ia",
    "logistica",
    "operacoes",
    "procurement",
    "reestruturacao",
    "sourcing",
    "supply chain",
    "suprimentos",
    "transformacao",
    "turnaround",
}


def _normalise(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text).casefold()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _matches_search_scope(title: str, queries: list[str]) -> bool:
    """Keep exact query matches plus creative senior domain titles."""
    normalised_title = _normalise(title)
    if not normalised_title:
        return False
    if any(
        (_normalise(query) in normalised_title or normalised_title in _normalise(query))
        for query in queries
        if _normalise(query)
    ):
        return True
    return any(term in normalised_title for term in _SENIOR_TITLE_TERMS) and any(
        term in normalised_title for term in _DOMAIN_TITLE_TERMS
    )


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = dateparser.parse(str(value))
        return parsed.replace(tzinfo=None) if parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


class GupyFeedSource(JobSource):
    @property
    def source_name(self) -> str:
        return "gupy"

    async def search(self, params: SearchParams) -> list[RawJob]:
        if not settings.gupy_feed_url:
            logger.debug("Gupy: no official partner feed configured, skipping")
            return []

        headers = {"Accept": "application/json"}
        if settings.gupy_feed_token:
            headers["Authorization"] = settings.gupy_feed_token

        try:
            timeout = aiohttp.ClientTimeout(total=GUPY_REQUEST_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(settings.gupy_feed_url, headers=headers) as response:
                    if response.status in {401, 403}:
                        logger.error("Gupy partner feed authorization failed: HTTP %d", response.status)
                        return []
                    if response.status != 200:
                        logger.warning("Gupy partner feed returned HTTP %d", response.status)
                        return []
                    payload = await response.json(content_type=None)
        except Exception as exc:
            logger.error("Gupy partner feed request failed: %s", exc)
            return []

        companies = payload.get("companies", []) if isinstance(payload, dict) else []
        requested_countries = {country.casefold() for country in params.countries}
        results: list[RawJob] = []
        seen: set[str] = set()

        for company in companies:
            if not isinstance(company, dict):
                continue
            company_name = company.get("name")
            company_url = str(company.get("url") or "").rstrip("/")
            company_key = company.get("subdomain") or _normalise(company_name) or "company"

            for item in company.get("jobs", []):
                if not isinstance(item, dict):
                    continue
                address = item.get("address") if isinstance(item.get("address"), dict) else {}
                country = str(address.get("country") or item.get("country") or "").strip().casefold()
                if requested_countries and country and country not in requested_countries:
                    continue

                query_country = country if country in requested_countries else "br"
                queries = params.queries_for_country(query_country)
                title = str(item.get("title") or item.get("name") or "").strip()
                if not _matches_search_scope(title, queries):
                    continue

                job_id = item.get("id")
                if job_id is None:
                    continue
                external_id = f"gupy_{company_key}_{job_id}"
                if external_id in seen:
                    continue
                seen.add(external_id)

                location = (
                    address.get("fullAddress")
                    or address.get("full-address")
                    or ", ".join(str(value) for value in (address.get("city"), address.get("region")) if value)
                    or item.get("location")
                    or None
                )
                description = " ".join(
                    str(item.get(field) or "") for field in ("description", "responsibilities", "prerequisites")
                ).strip()
                url = item.get("url")
                if not url and company_url:
                    url = f"{company_url}/jobs/{job_id}"

                results.append(
                    RawJob(
                        external_id=external_id,
                        source="gupy",
                        title=title,
                        company_name=company_name,
                        location=str(location) if location else None,
                        country=country.upper() if len(country) == 2 else None,
                        description=description or None,
                        url=str(url) if url else None,
                        is_remote=item.get("remoteWorking") if isinstance(item.get("remoteWorking"), bool) else None,
                        posted_at=_parse_date(item.get("publishingDate") or item.get("publishing-date")),
                        raw_data=item,
                    )
                )
                if len(results) >= GUPY_MAX_JOBS_PER_SCAN:
                    logger.warning(
                        "Gupy: capped feed at %d relevant jobs",
                        GUPY_MAX_JOBS_PER_SCAN,
                    )
                    return results

        logger.info("Gupy: %d relevant jobs fetched from official partner feed", len(results))
        return results
