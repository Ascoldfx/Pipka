"""BerlinStartupJobs RSS scraper.

Fetches the Operations & Support RSS feed from berlinstartupjobs.com.
For items whose title matches at least one search keyword, the full job
page is fetched to extract the complete description (RSS snippets are
only ~200 chars, too short for AI scoring).

URL pattern: https://berlinstartupjobs.com/<category>/feed/
Title format in feed: "Job Title // Company Name"
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp
from dateutil import parser as dateparser

from app.sources.base import JobSource, RawJob, SearchParams

logger = logging.getLogger(__name__)

# RSS feeds to pull from.  Operations is the primary one; we also grab
# Finance because CFO/Head of Finance roles appear there occasionally.
FEEDS = [
    "https://berlinstartupjobs.com/operations/feed/",
    "https://berlinstartupjobs.com/finance/feed/",
]

# Quick title-level keyword filter — fetch full page only for these
TITLE_KEYWORDS = [
    "operations", "supply chain", "procurement", "logistics", "coo",
    "chief operating", "head of ops", "vp ops", "director", "head of",
    "sourcing", "purchasing", "warehouse", "fulfillment", "inventory",
    "managing director", "general manager", "business operations",
    "category", "vendor", "distribution",
]


def _title_relevant(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in TITLE_KEYWORDS)


def _extract_company(title: str) -> tuple[str, str]:
    """Split 'Job Title // Company Name' → (title, company)."""
    parts = title.split(" // ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    # Fallback: try single '/' separator
    parts = title.split(" / ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _strip_html(html: str) -> str:
    """Very lightweight HTML → plain text."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _fetch_full_description(
    session: aiohttp.ClientSession, url: str
) -> str | None:
    """Fetch the job detail page and extract the description text."""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=12)
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as exc:
        logger.debug("BSJ detail fetch failed for %s: %s", url, exc)
        return None

    # BSJ uses a WP job listing plugin — content is in .job_description or .entry-content
    for pattern in [
        r'class="job_description"[^>]*>(.*?)</div>',
        r'class="entry-content"[^>]*>(.*?)</div>',
        r'<div[^>]+class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
    ]:
        m = re.search(pattern, html, re.S | re.I)
        if m:
            return _strip_html(m.group(1))[:4000]

    # Last resort: grab the largest <p> block
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    if paragraphs:
        longest = max(paragraphs, key=len)
        if len(longest) > 100:
            return _strip_html(longest)[:4000]
    return None


class BerlinStartupJobsSource(JobSource):
    @property
    def source_name(self) -> str:
        return "berlinstartupjobs"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; pipka-bot/1.0; job aggregator)"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            for feed_url in FEEDS:
                items = await self._fetch_feed(session, feed_url, params)
                for job in items:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)

        logger.info("BerlinStartupJobs: %d jobs fetched", len(results))
        return results

    async def _fetch_feed(
        self,
        session: aiohttp.ClientSession,
        feed_url: str,
        params: SearchParams,
    ) -> list[RawJob]:
        try:
            async with session.get(
                feed_url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.warning("BSJ feed %s returned %s", feed_url, resp.status)
                    return []
                text = await resp.text()
        except Exception as exc:
            logger.error("BSJ feed fetch failed (%s): %s", feed_url, exc)
            return []

        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            logger.error("BSJ XML parse error: %s", exc)
            return []

        jobs: list[RawJob] = []
        for item in root.findall(".//item"):
            try:
                raw_title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date_str = item.findtext("pubDate", "")
                desc_html = item.findtext("description", "")

                if not raw_title or not link:
                    continue

                job_title, company = _extract_company(raw_title)

                # Quick relevance gate — only fetch full page for matching titles
                if not _title_relevant(job_title):
                    # Also check params.queries for loose match
                    matched = False
                    tl = job_title.lower()
                    for q in params.queries:
                        if any(w in tl for w in q.lower().split() if len(w) > 4):
                            matched = True
                            break
                    if not matched:
                        continue

                # Parse publication date
                posted_at: datetime | None = None
                if pub_date_str:
                    try:
                        posted_at = dateparser.parse(pub_date_str).replace(tzinfo=None)
                    except Exception:
                        pass

                # Short RSS description as fallback
                short_desc = _strip_html(desc_html)

                # Fetch full description from job detail page
                full_desc = await _fetch_full_description(session, link)
                description = full_desc or short_desc or ""

                ext_id = f"bsj_{re.sub(r'[^a-z0-9]', '_', link.lower())[-60:]}"

                jobs.append(
                    RawJob(
                        external_id=ext_id,
                        source="berlinstartupjobs",
                        title=job_title,
                        company_name=company or None,
                        location="Berlin, Germany",
                        country="DE",
                        description=description,
                        url=link,
                        is_remote=None,
                        posted_at=posted_at,
                        raw_data={"feed_url": feed_url, "raw_title": raw_title},
                    )
                )
            except Exception as exc:
                logger.debug("BSJ item parse error: %s", exc)

        return jobs
