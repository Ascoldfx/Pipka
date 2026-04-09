from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.formatters import format_stats
from app.bot.keyboards import main_menu, status_keyboard
from app.database import async_session
from app.services.tracker_service import get_pipeline_stats, get_user_applications, update_status
from app.services.user_service import get_or_create_user


async def my_jobs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        apps = await get_user_applications(user.id, session)

    if not apps:
        await query.edit_message_text("У вас нет сохранённых вакансий.", reply_markup=main_menu())
        return

    lines = ["📋 Мои вакансии:\n"]
    for app in apps[:20]:
        job = app.job
        status_icon = {"saved": "💾", "applied": "📝", "interviewing": "🗣", "offer": "🎉", "rejected": "❌"}.get(app.status, "•")
        title = job.title if job else "N/A"
        company = job.company_name if job else ""
        lines.append(f"{status_icon} {title} — {company}")

    await query.edit_message_text("\n".join(lines), reply_markup=main_menu())


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        await session.commit()
        stats = await get_pipeline_stats(user.id, session)

    if not stats:
        await query.edit_message_text("Нет данных. Сохраните вакансии чтобы видеть статистику.", reply_markup=main_menu())
        return

    await query.edit_message_text(format_stats(stats), reply_markup=main_menu())


async def status_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")  # status_{app_id}_{new_status}
    if len(parts) < 3:
        return

    app_id = int(parts[1])
    new_status = parts[2]

    async with async_session() as session:
        app = await update_status(app_id, new_status, None, session)

    if app:
        await query.answer(f"Статус обновлён: {new_status}", show_alert=True)
    else:
        await query.answer("Ошибка обновления", show_alert=True)
