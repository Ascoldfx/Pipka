from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions, search_type_menu
from app.database import async_session
from app.services.job_service import search_and_score
from app.services.user_service import get_or_create_user
from app.sources.adzuna import AdzunaSource
from app.sources.arbeitsagentur import ArbeitsagenturSource
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources.jobspy_source import JobSpySource

logger = logging.getLogger(__name__)

SEARCH_PRESETS = {
    "search_regional": {
        "queries": ["Supply Chain", "Procurement", "Einkauf", "Logistik Manager", "Operations Manager"],
        "countries": ["de"],
        "locations": ["Leipzig", "Dresden", "Halle"],
        "label": "Саксония + Галле",
    },
    "search_germany": {
        "queries": ["Supply Chain Manager", "Procurement Manager", "Head of Logistics", "Operations Manager", "Einkauf Leiter"],
        "countries": ["de"],
        "locations": [],
        "label": "Вся Германия",
    },
    "search_europe": {
        "queries": ["Director Supply Chain", "VP Procurement", "Head of Logistics", "COO", "Global Operations"],
        "countries": ["de", "ch", "at", "nl"],
        "locations": [],
        "label": "Европа (International)",
    },
}


def _build_aggregator() -> JobAggregator:
    return JobAggregator([AdzunaSource(), JobSpySource(), ArbeitsagenturSource()])


async def search_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Выберите режим поиска:", reply_markup=search_type_menu())


async def search_preset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    preset_key = query.data
    preset = SEARCH_PRESETS.get(preset_key)
    if not preset:
        await query.edit_message_text("Неизвестный режим поиска.")
        return

    await query.edit_message_text(f"🚀 Ищу: *{preset['label']}*\\.\\.\\.\n\nИсточники: Adzuna, Indeed, LinkedIn, Google Jobs, Arbeitsagentur\nЭто может занять 30\\-60 секунд\\.", parse_mode="MarkdownV2")

    params = SearchParams(
        queries=preset["queries"],
        countries=preset["countries"],
        locations=preset.get("locations", []),
    )

    aggregator = _build_aggregator()
    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        results = await search_and_score(aggregator, params, user, session)

    if not results:
        await query.message.reply_text("Новых вакансий не найдено. Попробуйте другой режим.")
        return

    await query.message.reply_text(f"✅ Найдено {len(results)} вакансий (отсортированы по AI\\-скору):", parse_mode="MarkdownV2")

    for i, (job, score, verdict) in enumerate(results[:15]):
        card = format_job_card(job, score=score, rank=i + 1)
        if verdict:
            card += f"\n\n💬 _{verdict}_"
        try:
            await query.message.reply_text(card, parse_mode="Markdown", reply_markup=job_actions(job.id))
        except Exception as e:
            # Fallback without markdown if parsing fails
            await query.message.reply_text(card.replace("*", "").replace("_", ""), reply_markup=job_actions(job.id))
        await asyncio.sleep(0.3)


async def custom_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Напишите поисковый запрос (например: 'Data Engineer Berlin remote'):")
    context.user_data["awaiting_custom_search"] = True


async def text_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_custom_search"):
        return
    context.user_data["awaiting_custom_search"] = False

    text = update.message.text.strip()
    await update.message.reply_text(f"🚀 Ищу: *{text}*...", parse_mode="Markdown")

    params = SearchParams(queries=[text], countries=["de"], locations=[])

    aggregator = _build_aggregator()
    async with async_session() as session:
        user = await get_or_create_user(update.effective_user.id, update.effective_user.full_name, session)
        await session.commit()
        results = await search_and_score(aggregator, params, user, session)

    if not results:
        await update.message.reply_text("Ничего не найдено.")
        return

    for i, (job, score, verdict) in enumerate(results[:15]):
        card = format_job_card(job, score=score, rank=i + 1)
        if verdict:
            card += f"\n\n💬 _{verdict}_"
        try:
            await update.message.reply_text(card, parse_mode="Markdown", reply_markup=job_actions(job.id))
        except Exception:
            await update.message.reply_text(card.replace("*", "").replace("_", ""), reply_markup=job_actions(job.id))
        await asyncio.sleep(0.3)
