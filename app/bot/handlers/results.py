from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.database import async_session
from app.models.job import Job
from app.scoring.matcher import analyze_single_job
from app.services.tracker_service import save_job
from app.services.user_service import get_or_create_user

logger = logging.getLogger(__name__)


async def ai_analysis_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    job_id = int(query.data.replace("ai_", ""))
    original_text = query.message.text or ""
    await query.edit_message_text(text=f"{original_text}\n\n⏳ Анализирую...")

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()

        job = await session.get(Job, job_id)
        if not job:
            await query.edit_message_text(text=f"{original_text}\n\n❌ Вакансия не найдена")
            return

        profile = user.profile
        if not profile:
            await query.edit_message_text(text=f"{original_text}\n\n⚠️ Настройте профиль для анализа (/profile)")
            return

        analysis = await analyze_single_job(job, profile)

    # Send analysis as a new message (can be long, avoids message edit limits)
    await query.edit_message_text(text=f"{original_text}\n\n🤖 Анализ — см. ниже ⬇️")
    # Split if too long for Telegram (4096 char limit)
    full_text = f"🤖 АНАЛИЗ: {job.title}\n\n{analysis}"
    if len(full_text) <= 4096:
        await query.message.reply_text(full_text)
    else:
        await query.message.reply_text(full_text[:4096])
        await query.message.reply_text(full_text[4096:])


async def save_job_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    job_id = int(query.data.replace("save_", ""))

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        await save_job(user.id, job_id, session)

    await query.answer("💾 Вакансия сохранена!", show_alert=True)
