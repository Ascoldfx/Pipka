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
    "search_international": {
        "queries": [
            "Supply Chain Manager English",
            "Procurement Manager international",
            "Global Supply Chain Manager",
            "Head of Procurement",
            "VP Supply Chain",
        ],
        "countries": ["de"],
        "locations": [],
        "label": "International / English (DE)",
    },
    "search_europe": {
        "queries": ["Director Supply Chain", "VP Procurement", "Head of Logistics", "COO", "Global Operations", "Supply Chain Manager English"],
        "countries": ["de", "ch", "at", "nl"],
        "locations": [],
        "label": "Европа (DACH + NL)",
    },
}


def _build_aggregator() -> JobAggregator:
    return JobAggregator([AdzunaSource(), JobSpySource()])


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

    await query.edit_message_text(f"🚀 Ищу: {preset['label']}...\n\nИсточники: Adzuna, Indeed, LinkedIn, Google Jobs, Arbeitsagentur\nЭто может занять 30-60 секунд.")

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

    # Count sources
    source_counts: dict[str, int] = {}
    for job, _, _ in results:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1
    sources_str = " | ".join(f"{s}: {c}" for s, c in sorted(source_counts.items()))
    await query.message.reply_text(f"✅ Найдено {len(results)} вакансий (по AI-скору)\n📡 {sources_str}")

    for i, (job, score, verdict) in enumerate(results[:15]):
        card = format_job_card(job, score=score, rank=i + 1)
        if verdict:
            card += f"\n\n💬 {verdict}"
        await query.message.reply_text(card, reply_markup=job_actions(job.id))
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
            card += f"\n\n💬 {verdict}"
        await update.message.reply_text(card, reply_markup=job_actions(job.id))
        await asyncio.sleep(0.3)
