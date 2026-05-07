"""Lightweight in-process rate limiter for individual hot endpoints + a
global per-IP middleware.

Sliding-window counter keyed on (subject, key). Single-process only —
sufficient while we run one app container. When we go multi-replica, swap
this for a Redis-backed ``slowapi`` setup.

Usage::

    from app.api._ratelimit import check_rate_limit

    @router.get("/api/jobs/{job_id}/analyze")
    async def analyze_job(...):
        check_rate_limit(user_id=user.id, key="analyze", limit=30, window_s=3600)
        ...

Raises ``HTTPException(429)`` with a ``Retry-After`` header when the user
has exceeded the configured budget.

Memory: per-bucket eviction happens lazily on access, but a botnet hitting
unique IPs would leave dead buckets in ``_buckets`` forever. The
``start_bucket_cleanup_task`` lifespan hook runs hourly and drops fully
expired buckets.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# (subject, key) -> deque of monotonic-time floats, oldest at left.
# Subject is either ``("user", user_id)`` for authenticated buckets or
# ``("ip", "<addr>")`` for the global per-IP middleware.
_buckets: dict[tuple[tuple, str], deque[float]] = defaultdict(deque)
_lock = Lock()

# Hard ceiling on number of distinct buckets we'll track. Acts as a
# circuit breaker if the periodic cleanup task ever stops running. Each
# entry is ~200 bytes; 100k = ~20 MB ceiling — well under container memory.
_MAX_BUCKETS = 100_000


async def _bucket_cleanup_loop():
    """Drop fully-expired buckets once an hour.

    Without this, every distinct (ip, key) tuple a botnet probes leaves
    a dead empty deque in ``_buckets`` forever — slow memory leak,
    measurable at 5k-user prod scale after a few weeks of attacks.

    All our windows are ≤3600s, so any entry older than 1 hour is by
    definition expired regardless of the bucket's nominal limit.
    """
    while True:
        try:
            await asyncio.sleep(3600)
            now = time.monotonic()
            cutoff = now - 3600
            removed = 0
            with _lock:
                stale_keys: list = []
                for k, deq in _buckets.items():
                    while deq and deq[0] < cutoff:
                        deq.popleft()
                    if not deq:
                        stale_keys.append(k)
                for k in stale_keys:
                    del _buckets[k]
                removed = len(stale_keys)
            if removed:
                logger.info(
                    "rate-limit cleanup: removed %d stale buckets, %d alive",
                    removed, len(_buckets),
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("rate-limit cleanup loop crashed; will retry next tick")


def start_bucket_cleanup_task() -> asyncio.Task:
    """Spawn the cleanup loop. Caller (lifespan) holds the Task so it
    isn't GC'd."""
    return asyncio.create_task(_bucket_cleanup_loop(), name="rate-limit-cleanup")


def _check(subject: tuple, key: str, limit: int, window_s: int) -> int | None:
    """Return ``None`` if under the cap, otherwise the seconds-until-retry."""
    now = time.monotonic()
    cutoff = now - window_s
    bucket_key = (subject, key)
    with _lock:
        # Hard ceiling fallback: if the dict has somehow blown past
        # _MAX_BUCKETS (cleanup loop crashed and stayed crashed for days),
        # we don't materialise a new entry — return "rate-limited" for the
        # rest of this minute. Conservative but bounded memory.
        if bucket_key not in _buckets and len(_buckets) >= _MAX_BUCKETS:
            return window_s
        bucket = _buckets[bucket_key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return max(1, int(bucket[0] + window_s - now))
        bucket.append(now)
    return None


def check_rate_limit(*, user_id: int, key: str, limit: int, window_s: int) -> None:
    """Endpoint-level limiter keyed on user_id. Raises ``HTTPException(429)``
    with ``Retry-After`` when the user crosses the cap."""
    retry_after = _check(("user", user_id), key, limit, window_s)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit} per {window_s}s)",
            headers={"Retry-After": str(retry_after)},
        )


_TRUSTED_PROXY_HOSTS = frozenset({"127.0.0.1", "::1"})


def _client_ip(request: Request) -> str:
    """Best-effort client IP behind nginx + Cloudflare.

    Trust model: forwarded headers are honoured ONLY when the immediate
    socket peer is loopback (i.e. the request reached us via local nginx,
    not directly). Otherwise an attacker who can hit the app port directly
    could spoof ``CF-Connecting-IP: <random-uuid>`` per request and dodge
    the rate limiter — every call would look like a fresh IP.

    Order when peer is loopback:
        1. ``CF-Connecting-IP`` — Cloudflare's authoritative client IP.
        2. ``X-Forwarded-For[0]`` — nginx-supplied first hop.
        3. peer address itself (loopback — degraded, but better than '?').

    Order when peer is non-loopback:
        - peer address only. Headers ignored.
    """
    direct = request.client.host if request.client else None
    if direct in _TRUSTED_PROXY_HOSTS:
        cf = request.headers.get("cf-connecting-ip")
        if cf and cf.strip():
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",", 1)[0].strip()
        return direct
    return direct or "unknown"


# Per-IP global limits. Tuned for the 5k-user prod target — a healthy SPA
# user issues maybe 1-3 req/s during bursts, so 200/min covers normal traffic
# while a script-runner gets shut out fast. Auth-mutating endpoints get a
# separate, much tighter bucket.
_IP_LIMITS: tuple[tuple[str, int, int, tuple[str, ...]], ...] = (
    # (key,                                limit, window_s, path-prefixes)
    ("auth-write",                            10,    60,   ("/auth/google/login", "/auth/logout")),
    ("profile-write",                         20,    60,   ("/api/profile",)),
    ("api-global",                           300,    60,   ("/api/",)),
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Process-wide per-IP sliding-window limiter.

    Applied as a middleware so we don't need to plumb checks into every
    router. Looks up the most-specific matching bucket from ``_IP_LIMITS``
    (first match wins — order tightest-to-loosest). ``GET`` requests still
    consume the bucket because crawlers / scrapers also pull data, but auth
    callbacks and static assets are exempt.

    Returns 429 JSON with a ``Retry-After`` header. Per-user endpoint limits
    (e.g. ``check_rate_limit(key='analyze', ...)`` in jobs.py) still apply
    on top of this.
    """

    EXEMPT_PREFIXES = ("/static/", "/health", "/auth/google/callback")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        ip = _client_ip(request)

        for key, limit, window_s, prefixes in _IP_LIMITS:
            if any(path.startswith(p) for p in prefixes):
                retry_after = _check(("ip", ip), key, limit, window_s)
                if retry_after is not None:
                    return JSONResponse(
                        {"detail": f"Rate limit exceeded ({limit} per {window_s}s)"},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
                break  # first-match-wins; don't double-bill
        return await call_next(request)
