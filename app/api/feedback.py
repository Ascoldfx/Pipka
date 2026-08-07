from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User, UserFeedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackRequest(BaseModel):
    category: str = Field("general", description="general, bug, feature, question")
    message: str = Field(..., min_length=5, max_length=4000)
    contact: str | None = Field(None, max_length=255)


@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Submit user feedback or bug report."""
    fb = UserFeedback(
        user_id=current_user.id,
        category=req.category.strip(),
        message=req.message.strip(),
        contact=req.contact.strip() if req.contact else current_user.email,
    )
    session.add(fb)
    await session.commit()
    await session.refresh(fb)

    logger.info("New user feedback from user_id=%s category=%s", current_user.id, req.category)
    return {"ok": True, "id": fb.id, "message": "Спасибо за ваш отзыв! Мы ценим ваше мнение."}


@router.post("/onboarding/complete")
async def complete_onboarding(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark onboarding as completed for current user."""
    current_user.onboarded = True
    await session.commit()
    return {"ok": True, "onboarded": True}
