"""Lightweight in-process rate limiter for individual hot endpoints.

Sliding-window counter keyed on user_id. Single-process only — sufficient
while we run one app container. When we go multi-replica, swap this for a
Redis-backed ``slowapi`` setup.

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

# (user_id, key) -> deque of monotonic-time floats, oldest at left.
_buckets: dict[tuple[int, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def check_rate_limit(*, user_id: int, key: str, limit: int, window_s: int) -> None:
    """Raise 429 if ``user_id`` has issued ``>= limit`` calls under ``key``
    within the last ``window_s`` seconds. Otherwise records this call.

    The implementation is a simple sliding window over a deque — accurate to
    the second and O(window-eviction) per call. Memory is bounded by
    ``limit`` entries per (user, key) pair.
    """
    now = time.monotonic()
    cutoff = now - window_s
    bucket_key = (user_id, key)

    with _lock:
        bucket = _buckets[bucket_key]
        # Drop expired entries from the left
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            # Suggest a retry time based on the oldest in-window entry
            retry_after = max(1, int(bucket[0] + window_s - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit} per {window_s}s)",
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
