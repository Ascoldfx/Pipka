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
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# (subject, key) -> deque of monotonic-time floats, oldest at left.
# Subject is either ``("user", user_id)`` for authenticated buckets or
# ``("ip", "<addr>")`` for the global per-IP middleware.
_buckets: dict[tuple[tuple, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def _check(subject: tuple, key: str, limit: int, window_s: int) -> int | None:
    """Return ``None`` if under the cap, otherwise the seconds-until-retry."""
    now = time.monotonic()
    cutoff = now - window_s
    bucket_key = (subject, key)
    with _lock:
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


def _client_ip(request: Request) -> str:
    """Best-effort client IP behind nginx + Cloudflare.

    Order: Cloudflare's ``CF-Connecting-IP`` (only sent when origin is
    actually behind CF; we trust it because Cloudflare is the only public
    front for pipka.net), then first hop of ``X-Forwarded-For`` (set by
    nginx), then the raw socket address as a last resort.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # First IP is the original client; the rest are intermediate proxies.
        return xff.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


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
