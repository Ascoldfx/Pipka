"""URL-liveness checker — daily HEAD-ping to flag closed job postings.

Why
---
``_cleanup_old_jobs`` evicts jobs by age (>45 days), but a posting filled in
its first week sits in the inbox for the rest of those 45 days. This service
runs an HTTP HEAD against each ``Job.url`` and classifies the response into:

* **active**       — 2xx OK
* **closed**       — 404, 410, or a 3xx redirect that lands on a generic
                     listing/search page (typical "this job no longer exists"
                     pattern across LinkedIn, Indeed, Xing).
* **unreachable**  — set after ``url_check_max_failures`` consecutive
                     transient errors (5xx, network errors, timeouts). Not
                     hidden in UI by default — reserved for "we genuinely
                     can't tell".

Status ``None`` means "never checked yet" — readers (dashboard) treat that as
active so freshly scraped jobs appear immediately.

Concurrency
-----------
* Process-wide ``Semaphore(N)`` caps in-flight requests.
* Per-host async lock + monotonic-time pacer enforces ``per_host_delay``
  between any two requests to the same host. Critical because LinkedIn /
  Indeed actively rate-limit and a 200-job burst against a single host would
  trigger captchas or IP blocks.

Soft-404 detection (e.g. HTTP 200 with body "this position has been filled")
is intentionally out of scope for the MVP — see [[Roadmap]].
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job

logger = logging.getLogger(__name__)

# A polite, identifiable user-agent. Some sites are friendlier when they can
# tell you're a bot rather than a hijacked browser; this also makes the
# requests trivially blockable if any owner asks.
USER_AGENT = "Pipka-Liveness/1.0 (+https://pipka.net)"


# Per-host throttling state — shared across the whole process so multiple
# scheduler ticks don't stomp each other (unlikely, but free correctness).
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_last_host_request: dict[str, float] = defaultdict(float)


def _classify_redirect(target: str | None) -> str:
    """Return ``'closed'`` if ``target`` looks like a job-board "vacancy gone"
    fallback (root, /jobs, /search, /careers without ID), else ``'active'``.

    Many job boards 302 a removed posting to a generic listing page. We
    detect this by scoring the path: short paths with no numeric/uuid token
    are treated as "swept under the rug".
    """
    if not target:
        return "closed"  # 3xx with no Location header == implementation bug, treat as gone

    parsed = urlparse(target)
    path = (parsed.path or "/").rstrip("/")

    # Bare root
    if path in ("", "/"):
        return "closed"

    # Generic listing endpoints — exact match (no job-id appended)
    GENERIC_LANDINGS = {
        "/jobs", "/careers", "/search", "/job-search",
        "/career", "/opportunities", "/positions",
        "/de/jobs", "/en/jobs", "/uk/jobs",
    }
    if path.lower() in GENERIC_LANDINGS:
        return "closed"

    # Heuristic: a "real" job URL almost always carries a numeric or
    # UUID-shaped token somewhere in the path. /search?q=... doesn't count.
    has_id_token = any(
        seg.isdigit() or len(seg) >= 8 and any(c.isdigit() for c in seg)
        for seg in path.split("/")
        if seg
    )
    return "active" if has_id_token else "closed"


async def _pace_host(host: str) -> None:
    """Sleep until ``per_host_delay`` has passed since the last request to ``host``."""
    async with _host_locks[host]:
        elapsed = time.monotonic() - _last_host_request[host]
        if elapsed < settings.url_check_per_host_delay:
            await asyncio.sleep(settings.url_check_per_host_delay - elapsed)
        _last_host_request[host] = time.monotonic()


async def check_url(url: str, client: httpx.AsyncClient) -> tuple[str, bool]:
    """Probe ``url`` and classify it.

    Returns ``(status, transient)``:
        * ``status``    — ``'active' | 'closed' | 'unreachable'``.
        * ``transient`` — ``True`` if the failure was network-level (5xx,
                          timeout, conn error). Caller should bump the
                          consecutive-failures counter rather than treating
                          this as a definitive "gone".
    """
    try:
        host = urlparse(url).netloc
    except Exception:
        return "closed", False  # malformed URL → treat as gone

    if not host:
        return "closed", False

    await _pace_host(host)

    try:
        # follow_redirects=False — we want to inspect the Location header
        # ourselves so we can detect "redirect to /jobs" patterns.
        resp = await client.head(url, follow_redirects=False)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        logger.debug("url-check transient failure %s: %s", url, type(exc).__name__)
        return "unreachable", True
    except httpx.RequestError as exc:
        # Things like InvalidURL, UnsupportedProtocol — definitively dead.
        logger.debug("url-check hard failure %s: %s", url, exc)
        return "closed", False

    status = resp.status_code

    if 200 <= status < 300:
        return "active", False
    if status in (404, 410):
        return "closed", False
    if 300 <= status < 400:
        # Some sites refuse HEAD with 405 and respond with a 3xx to a login
        # page — distinguishing that from a real "removed" redirect needs
        # GET semantics. Cheap heuristic: classify by Location.
        return _classify_redirect(resp.headers.get("location")), False
    if status in (401, 403):
        # Auth-required page is *probably* still alive (the listing exists,
        # we just can't see it). Don't mark it closed.
        return "active", False
    if status == 405:
        # Method Not Allowed → fall back to GET, but only one round to avoid
        # hammering. Treat unknown as active rather than bogus-closing it.
        try:
            resp = await client.get(url, follow_redirects=False)
        except Exception:
            return "unreachable", True
        if 200 <= resp.status_code < 300:
            return "active", False
        if resp.status_code in (404, 410):
            return "closed", False
        if 300 <= resp.status_code < 400:
            return _classify_redirect(resp.headers.get("location")), False
        if resp.status_code >= 500:
            return "unreachable", True
        return "active", False
    if status >= 500:
        return "unreachable", True

    # Catch-all: don't false-positive close.
    return "active", False


async def run_url_check_pass(session: AsyncSession) -> dict[str, int]:
    """One scheduler tick: pick the oldest-checked ``url_check_per_run`` jobs,
    HEAD-ping them, and persist the result.

    Returns counters: ``{checked, active, closed, unreachable, skipped}``.
    """
    if not settings.url_check_enabled:
        return {"checked": 0, "active": 0, "closed": 0, "unreachable": 0, "skipped": 0}

    cutoff = datetime.now() - timedelta(hours=settings.url_check_recheck_hours)

    # Picker:
    #   * Skip jobs without a URL (nothing to ping).
    #   * Skip jobs already checked within the recheck window.
    #   * NULLS FIRST — never-checked jobs go first.
    result = await session.execute(
        select(Job)
        .where(
            Job.url.isnot(None),
            Job.url != "",
            or_(Job.url_checked_at.is_(None), Job.url_checked_at < cutoff),
        )
        .order_by(Job.url_checked_at.asc().nullsfirst())
        .limit(settings.url_check_per_run)
    )
    jobs = result.scalars().all()
    if not jobs:
        return {"checked": 0, "active": 0, "closed": 0, "unreachable": 0, "skipped": 0}

    semaphore = asyncio.Semaphore(settings.url_check_concurrency)
    counts = {"checked": 0, "active": 0, "closed": 0, "unreachable": 0, "skipped": 0}

    timeout = httpx.Timeout(settings.url_check_timeout_seconds)
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}

    async with httpx.AsyncClient(timeout=timeout, headers=headers, http2=False) as client:
        async def _one(job: Job) -> None:
            async with semaphore:
                try:
                    new_status, transient = await check_url(job.url, client)
                except Exception:
                    logger.exception("url-check unexpected error for job %s", job.id)
                    counts["skipped"] += 1
                    return

                now = datetime.now()
                if transient:
                    job.url_check_failures = (job.url_check_failures or 0) + 1
                    if job.url_check_failures >= settings.url_check_max_failures:
                        job.url_status = "unreachable"
                        counts["unreachable"] += 1
                    else:
                        # Don't update status yet — wait for confirmation.
                        counts["unreachable"] += 1
                else:
                    job.url_status = new_status
                    job.url_check_failures = 0
                    counts[new_status] = counts.get(new_status, 0) + 1

                job.url_checked_at = now
                counts["checked"] += 1

        await asyncio.gather(*(_one(j) for j in jobs))

    await session.commit()
    return counts
