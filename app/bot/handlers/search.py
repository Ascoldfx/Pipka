from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.formatters import format_job_card
from app.bot.keyboards import job_actions, search_type_menu, show_more_button
from app.database import async_session
from app.services.job_service import search_and_score
from app.services.user_service import get_or_create_user
from app.sources.aggregator import JobAggregator
from app.sources.base import SearchParams
from app.sources import AdzunaSource, JobSpySource, ArbeitnowSource, RemotiveSource, ArbeitsagenturSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 15

SEARCH_PRESETS = {
    "search_regional": {
        "queries": [
            "Director Supply Chain",
            "Head of Procurement",
            "Head of Supply Chain",
            "VP Operations",
            "Director Logistics",
            "Head of Operations",
            "Direktor Einkauf",
            "Leiter Supply Chain",
        ],
        "countries": ["de"],
        "locations": ["Leipzig", "Dresden", "Halle", "Berlin"],
        "label": "Регион (Саксония + Берлин)",
    },
    "search_germany": {
        "queries": [
            "Director Supply Chain",
            "Head of Procurement",
            "VP Supply Chain",
            "Director Operations",
            "Head of Logistics",
            "Chief Operating Officer",
            "VP Procurement",
            "Director Purchasing",
            "Head of Sourcing",
            "Director Einkauf",
        ],
        "countries": ["de"],
        "locations": [],
        "label": "Вся Германия",
    },
    "search_international": {
        "queries": [
            "Director Supply Chain English",
            "Head of Procurement international",
            "VP Supply Chain English",
            "Global Supply Chain Director",
            "Chief Procurement Officer",
            "VP Operations international",
            "Director Global Sourcing",
            "Head of Supply Chain English",
            "Chief Operating Officer international",
            "Director Procurement English",
            "VP Logistics international",
            "Head of Operations English",
        ],
        "countries": ["de"],
        "locations": [],
        "label": "International / English (DE)",
    },
    "search_europe": {
        "queries": [
            "Director Supply Chain",
            "VP Procurement",
            "Head of Logistics",
            "Chief Operating Officer",
            "Global Operations Director",
            "Chief Procurement Officer",
            "Head of Supply Chain English",
            "VP Operations international",
            "Director Sourcing",
            "Head of Purchasing",
        ],
        "countries": ["de", "at", "nl", "ch", "be", "si", "sk", "ro", "hu"],
        "locations": [],
        "label": "Европа (DACH + NL + CEE)",
    },
    "search_cee": {
        "queries": [
            "Director Supply Chain",
            "Head of Procurement",
            "VP Operations",
            "Director Logistics",
            "Chief Operating Officer",
            "Global Supply Chain Director",
        ],
        "countries": ["si", "sk", "ro", "hu"],
        "locations": [],
        "label": "CEE (SI/SK/RO/HU)",
    },
}


def _build_aggregator() -> JobAggregator:
    return JobAggregator([AdzunaSource(), JobSpySource(), ArbeitnowSource(), RemotiveSource(), ArbeitsagenturSource()])


async def search_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Выберите режим поиска:", reply_markup=search_type_menu())


async def search_preset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    preset_key = query.data
    
    if preset_key == "search_profile":
        async with async_session() as session:
            import sqlalchemy as sa
            from app.models.user import User
            from sqlalchemy.orm import selectinload
            
            user_res = await session.execute(
                sa.select(User).options(selectinload(User.profile)).where(User.telegram_id == update.effective_user.id)
            )
            user = user_res.scalar_one_or_none()
            
            if not user or not user.profile or not user.profile.preferred_countries or not user.profile.target_titles:
                await query.edit_message_text(
                    "❌ Ваш профиль или настройки поиска не заполнены!\n\n"
                    "Зайдите на вкладку Settings на сайте и заполните поля `Target job titles` и `Countries`."
                )
                return
                
            preset = {
                "label": "Мой Профиль",
                "queries": user.profile.target_titles,
                "countries": user.profile.preferred_countries,
                "locations": [],
            }
    else:
        preset = SEARCH_PRESETS.get(preset_key)
        if not preset:
            await query.edit_message_text("Неизвестный режим поиска.")
            return

    await query.edit_message_text(
        f"🚀 Ищу: {preset['label']}...\n"
        f"Страны: {', '.join(preset['countries']).upper()}\n"
        f"Запросы: {len(preset['queries'])} шт.\n\n"
        "Источники: Adzuna, JobSpy, Arbeitnow, Remotive\n"
        "Это может занять 1-3 минуты."
    )

    params = SearchParams(
        queries=preset["queries"],
        countries=preset["countries"],
        locations=preset.get("locations", []),
    )

    aggregator = _build_aggregator()
    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        results = await search_and_score(aggregator, params, user, session, max_results=100)

    if not results:
        await query.message.reply_text("Новых вакансий не найдено. Попробуйте другой режим.")
        return

    # Store all results for pagination
    context.user_data["search_results"] = results
    context.user_data["search_page"] = 0

    # Summary
    source_counts: dict[str, int] = {}
    for job, _, _ in results:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1
    sources_str = " | ".join(f"{s}: {c}" for s, c in sorted(source_counts.items()))

    top_count = sum(1 for _, score, _ in results if score >= 70)
    good_count = sum(1 for _, score, _ in results if 40 <= score < 70)

    summary = f"✅ Найдено {len(results)} вакансий\n"
    if top_count:
        summary += f"🟢 Топ (70+): {top_count}\n"
    if good_count:
        summary += f"🟡 Средние (40-69): {good_count}\n"
    summary += f"📡 {sources_str}"
    await query.message.reply_text(summary)

    # Show first page
    await _send_results_page(query.message, context, results, page=0)


async def show_more_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Show more' button."""
    query = update.callback_query
    await query.answer()

    results = context.user_data.get("search_results")
    if not results:
        await query.edit_message_text("Результаты поиска устарели. Запустите новый поиск.")
        return

    page = context.user_data.get("search_page", 0) + 1
    context.user_data["search_page"] = page

    start = page * PAGE_SIZE
    if start >= len(results):
        await query.edit_message_text("Больше вакансий нет. Попробуйте другой режим поиска.")
        return

    # Remove the "show more" button from previous message
    await query.edit_message_text(f"📥 Загружаю вакансии {start + 1}-{min(start + PAGE_SIZE, len(results))}...")

    await _send_results_page(query.message, context, results, page=page)


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

    # Show "more" button if there are more results
    remaining = len(results) - (start + len(page_results))
    if remaining > 0:
        await message.reply_text(
            f"Показано {start + len(page_results)} из {len(results)} вакансий",
            reply_markup=show_more_button(),
        )


async def custom_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Напишите поисковый запрос (например: 'Director Supply Chain Berlin'):")
    context.user_data["awaiting_custom_search"] = True


async def text_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_custom_search"):
        return
    context.user_data["awaiting_custom_search"] = False

    text = update.message.text.strip()
    await update.message.reply_text(f"🚀 Ищу: {text}...\nЭто может занять 30-60 секунд.")

    params = SearchParams(queries=[text], countries=["de"], locations=[])

    aggregator = _build_aggregator()
    async with async_session() as session:
        user = await get_or_create_user(update.effective_user.id, update.effective_user.full_name, session)
        await session.commit()
        results = await search_and_score(aggregator, params, user, session, max_results=100)

    if not results:
        await update.message.reply_text("Ничего не найдено.")
        return

    # Store for pagination
    context.user_data["search_results"] = results
    context.user_data["search_page"] = 0

    top_count = sum(1 for _, score, _ in results if score >= 70)
    await update.message.reply_text(
        f"✅ Найдено {len(results)} вакансий" + (f" (🟢 топ: {top_count})" if top_count else "")
    )

    await _send_results_page(update.message, context, results, page=0)
