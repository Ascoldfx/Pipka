"""Shared auth/role helpers for the dashboard API routers.

Internal module — leading underscore signals "implementation detail of the
api package". Each router under ``app/api/`` imports the helpers it needs
rather than duplicating the auth dance per file.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User

VALID_ACTIONS = {"save", "applied", "reject"}
VALID_WORK_MODES = {"remote", "hybrid", "onsite", "any"}


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


def get_role(request: Request, user: User | None) -> str:
    """Resolve the caller's role: session role → DB role → 'guest'."""
    session_role = request.session.get("user_role")
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


def require_admin(request: Request) -> None:
    """Raise 403 if the caller is not an admin."""
    if request.session.get("user_role", "guest") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
