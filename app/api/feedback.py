from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_current_user
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

    # Send instant Telegram notification to admin Telegram IDs
    try:
        from app.config import settings
        import httpx

        admin_ids = [
            tid.strip()
            for tid in settings.allowed_telegram_ids.split(",")
            if tid.strip().isdigit()
        ]
        if admin_ids and settings.telegram_bot_token:
            category_icon = {
                "bug": "🐛 БАГ / ОШИБКА",
                "feature": "💡 ИДЕЯ / ПРЕДЛОЖЕНИЕ",
                "question": "❓ ВОПРОС",
                "general": "💬 ОТЗЫВ",
            }.get(req.category.strip().lower(), "💬 ОТЗЫВ")

            user_disp = current_user.name or current_user.email or f"ID {current_user.id}"
            contact_disp = req.contact.strip() if req.contact else (current_user.email or "—")

            tg_text = (
                f"📩 <b>Новая Обратная Связь!</b>\n\n"
                f"<b>Тип:</b> {category_icon}\n"
                f"<b>От:</b> {user_disp}\n"
                f"<b>Контакт:</b> <code>{contact_disp}</code>\n\n"
                f"<b>Сообщение:</b>\n<i>{req.message.strip()}</i>"
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                for aid in admin_ids:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                        json={"chat_id": int(aid), "text": tg_text, "parse_mode": "HTML"},
                    )
    except Exception as exc:
        logger.debug("Failed to send Telegram feedback notification: %s", exc)

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
