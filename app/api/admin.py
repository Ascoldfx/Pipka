"""Admin endpoints — user inspection and soft-delete."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api._helpers import require_admin
from app.database import async_session
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/admin/user/{user_id}/profile")
async def admin_get_user_profile(request: Request, user_id: int):
    """Admin only: fetch full profile and user info for a specific user ID."""
    require_admin(request)
    async with async_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        p = user.profile
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "is_active": user.is_active,
            "joined": user.created_at.isoformat() if user.created_at else None,
            "profile": {
                "resume_text": p.resume_text if p else None,
                "target_titles": p.target_titles if p else [],
                "min_salary": p.min_salary if p else None,
                "languages": p.languages if p else {},
                "experience_years": p.experience_years if p else None,
                "work_mode": p.work_mode if p else "any",
                "preferred_countries": p.preferred_countries if p else [],
                "excluded_keywords": p.excluded_keywords if p else [],
                "english_only": p.english_only if p else False,
                "target_companies": p.target_companies if p else [],
            } if p else None,
        }


@router.delete("/api/admin/user/{user_id}")
async def admin_delete_user(request: Request, user_id: int):
    """Admin only: soft-delete a user (sets ``is_active=False``)."""
    require_admin(request)
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.is_active = False
        await session.commit()
        return {"ok": True}
