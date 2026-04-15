from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions, show_more_button, inbox_menu
from app.database import async_session
from app.services.tracker_service import get_unreviewed_jobs
from app.services.user_service import get_or_create_user

logger = logging.getLogger(__name__)

PAGE_SIZE = 15

async def inbox_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📬 Экран Inbox:\nКакие вакансии показать?", reply_markup=inbox_menu())


async def inbox_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Determine filter from callback data: inbox_all, inbox_good, inbox_top
    min_score = 0
    if query.data == "inbox_good":
        min_score = 40
    elif query.data == "inbox_top":
        min_score = 70

    await query.edit_message_text(f"📬 Собираю непросмотренные вакансии...")

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        results = await get_unreviewed_jobs(user.id, session, min_score)

    if not results:
        await query.edit_message_text("🥳 Новых непросмотренных вакансий нет!")
        return

    # Store all results for pagination
    context.user_data["search_results"] = results
    context.user_data["search_page"] = 0

    top_count = sum(1 for _, score, _ in results if score >= 70)
    good_count = sum(1 for _, score, _ in results if 40 <= score < 70)

    summary = f"📬 В инбоксе {len(results)} вакансий:\n"
    if top_count:
        summary += f"🟢 Топ (70+): {top_count}\n"
    if good_count:
        summary += f"🟡 Средние (40-69): {good_count}\n"
        
    await query.message.reply_text(summary)

    # Use similar logic to send first page
    await _send_results_page(query.message, context, results, page=0)


async def _send_results_page(message, context, results, page: int):
    """Send a page of results."""
    start = page * PAGE_SIZE
    page_results = results[start : start + PAGE_SIZE]

    for i, (job, score, verdict) in enumerate(page_results):
        rank = start + i + 1
        card = format_job_card(job, score=score, rank=rank)
        if verdict:
            card += f"\n\n💬 {verdict}"
        await message.reply_text(card, reply_markup=job_actions(job.id))
        await asyncio.sleep(0.3)

    remaining = len(results) - (start + len(page_results))
    if remaining > 0:
        await message.reply_text(
            f"Показано {start + len(page_results)} из {len(results)} вакансий",
            reply_markup=show_more_button(),
        )
