from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards import main_menu
from app.database import async_session
from app.services.user_service import get_or_create_user


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    async with async_session() as session:
        await get_or_create_user(tg_user.id, tg_user.full_name, session)
        await session.commit()

    text = (
        "🤖 *JobHunt Bot*\n\n"
        "Агрегатор вакансий из 5\\+ площадок с AI\\-скорингом\\.\n\n"
        "Источники: Adzuna, Indeed, LinkedIn, Google Jobs, Arbeitsagentur\n\n"
        "Начните с настройки профиля или сразу ищите\\!"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=main_menu())
    else:
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=main_menu())


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Команды:*\n"
        "/start \\- Главное меню\n"
        "/search \\- Поиск вакансий\n"
        "/profile \\- Настройки профиля\n"
        "/my\\_jobs \\- Сохранённые вакансии\n"
        "/stats \\- Статистика pipeline\n"
        "/help \\- Эта справка",
        parse_mode="MarkdownV2",
    )
