"""Shared auth/role helpers for the dashboard API routers.

Internal module — leading underscore signals "implementation detail of the
api package". Each router under ``app/api/`` imports the helpers it needs
rather than duplicating the auth dance per file.
"""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.user import User

VALID_ACTIONS = {"save", "applied", "reject"}
VALID_WORK_MODES = {"remote", "hybrid", "onsite", "any"}

# Admin-role cache: { user_id: (role, expires_at_monotonic) }.
# We re-fetch role from DB on every protected request, but only once per
# 60s per user — so a 5k-user prod doesn't hammer the DB. Compromise: an
# admin who lost their role mid-session keeps it for ≤60s after revocation.
_ROLE_CACHE: dict[int, tuple[str, float]] = {}
_ROLE_TTL_SECONDS = 60.0


def _drop_role_cache(user_id: int) -> None:
    """Public hook for admin endpoints to drop a user's cached role
    immediately after revoking it. Call site: admin.py:admin_delete_user."""
    _ROLE_CACHE.pop(user_id, None)


async def get_session_user(request: Request, session) -> User | None:
    """Fetch the logged-in User (with profile eager-loaded) from the session
    cookie, or ``None`` if not authenticated / inactive."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    result = await session.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    return result.scalar_one_or_none()


# Alias kept for symmetry with old dashboard.py — both names referenced in
# call sites; consolidating to one name during the split would have inflated
# the diff. Kept as a thin pass-through.
get_user = get_session_user


async def _resolve_role_from_db(user_id: int) -> str:
    """Read role straight from the DB, with a 60-second per-user cache.

    Why not trust the session: ``request.session["user_role"]`` is set once
    at login and never refreshed. A 30-day cookie keeps an ex-admin admin
    long after we revoke their role in the DB. Hitting the DB on every
    protected request would hurt at 5k users — hence the 60s LRU.
    """
    now = time.monotonic()
    cached = _ROLE_CACHE.get(user_id)
    if cached and cached[1] > now:
        return cached[0]

    async with async_session() as session:
        result = await session.execute(
            select(User.role, User.is_active).where(User.id == user_id)
        )
        row = result.one_or_none()

    if row is None or not row.is_active:
        # User got deleted or deactivated since session was issued.
        role = "guest"
    else:
        role = row.role or "user"

    _ROLE_CACHE[user_id] = (role, now + _ROLE_TTL_SECONDS)
    return role


def get_role(request: Request, user: User | None) -> str:
    """Synchronous role lookup — session-only, used in non-async paths.

    Prefer ``require_admin_async`` / ``require_authenticated`` for protected
    endpoints — those validate against the DB. This sync helper is safe for
    cosmetic UI checks (e.g. "show admin nav?") where a 60s lag in role
    revocation is acceptable.
    """
    session_role = request.session.get("user_role") or None
    if session_role:
        return session_role
    if user and user.role:
        return user.role
    return "guest"


def require_authenticated(request: Request) -> None:
    """Raise 401 if there is no user session."""
    if not request.session.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required"
        )


async def require_admin_async(request: Request) -> None:
    """Async-aware admin check. Validates role against the DB (cached 60s)
    so a revoked admin loses access within a minute, not a month.

    Replaces the legacy session-only ``require_admin`` for endpoints that
    can run async. The sync version is kept for pre-existing call sites
    until they migrate.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required"
        )
    role = await _resolve_role_from_db(user_id)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


def require_admin(request: Request) -> None:
    """Synchronous admin check — DEPRECATED for protected actions.

    Trusts the session cookie (set once at login, never refreshed). Use
    only for cheap UI tweaks. For real authorisation use
    ``require_admin_async`` which round-trips the DB on a 60s TTL.
    """
    if (request.session.get("user_role") or "guest") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
