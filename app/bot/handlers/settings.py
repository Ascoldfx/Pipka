from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards import main_menu, profile_menu
from app.database import async_session
from app.services.user_service import ensure_profile, get_or_create_user, update_profile

# Profile field being edited
PROFILE_FIELDS = {
    "prof_resume": ("resume_text", "📝 Отправьте текст резюме (краткое описание опыта):"),
    "prof_titles": ("target_titles", "🎯 Целевые должности через запятую (напр: Supply Chain Manager, Procurement Lead):"),
    "prof_salary": ("min_salary", "💰 Минимальная годовая зарплата (EUR, число):"),
    "prof_languages": ("languages", "🌐 Языки в формате: EN:C1, DE:B1, RU:native"),
    "prof_location": ("base_location", "📍 Ваш город (напр: Leipzig):"),
    "prof_industries": ("industries", "🏭 Индустрии через запятую (напр: Manufacturing, FMCG, Automotive):"),
}


async def profile_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    async with async_session() as session:
        user = await get_or_create_user(query.from_user.id, query.from_user.full_name, session)
        profile = await ensure_profile(user, session)
        await session.commit()

    lines = ["⚙️ Ваш профиль:\n"]
    lines.append(f"📝 Резюме: {'✅' if profile.resume_text else '❌ не задано'}")
    lines.append(f"🎯 Должности: {', '.join(profile.target_titles) if profile.target_titles else '❌'}")
    lines.append(f"💰 Мин. зарплата: {profile.min_salary or '❌'}")
    lines.append(f"🌐 Языки: {profile.languages or '❌'}")
    lines.append(f"📍 Локация: {profile.base_location or '❌'}")
    lines.append(f"🏭 Индустрии: {', '.join(profile.industries) if profile.industries else '❌'}")

    await query.edit_message_text("\n".join(lines), reply_markup=profile_menu())


async def profile_field_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field_key = query.data
    if field_key not in PROFILE_FIELDS:
        return

    field_name, prompt_text = PROFILE_FIELDS[field_key]
    context.user_data["editing_profile_field"] = field_name
    await query.edit_message_text(prompt_text)


async def profile_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field_name = context.user_data.get("editing_profile_field")
    if not field_name:
        return

    context.user_data.pop("editing_profile_field")
    text = update.message.text.strip()

    async with async_session() as session:
        user = await get_or_create_user(update.effective_user.id, update.effective_user.full_name, session)

        # Parse field values
        if field_name == "target_titles":
            value = [t.strip() for t in text.split(",") if t.strip()]
        elif field_name == "min_salary":
            try:
                value = int(text.replace(" ", "").replace(",", ""))
            except ValueError:
                await update.message.reply_text("❌ Введите число.")
                return
        elif field_name == "languages":
            value = {}
            for part in text.split(","):
                part = part.strip()
                if ":" in part:
                    lang, level = part.split(":", 1)
                    value[lang.strip().lower()] = level.strip().upper()
        elif field_name == "industries":
            value = [t.strip() for t in text.split(",") if t.strip()]
        else:
            value = text

        await update_profile(user, session, **{field_name: value})

    await update.message.reply_text("✅ Профиль обновлён!", reply_markup=main_menu())
