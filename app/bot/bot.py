import logging

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import settings
from app.bot.handlers.start import start_handler, help_handler
from app.bot.handlers.search import (
    custom_search_handler,
    search_menu_handler,
    search_preset_handler,
    text_search_handler,
)
from app.bot.handlers.results import ai_analysis_handler, save_job_handler
from app.bot.handlers.tracker import my_jobs_handler, stats_handler, status_update_handler
from app.bot.handlers.settings import profile_field_handler, profile_menu_handler, profile_text_handler

logger = logging.getLogger(__name__)


def create_bot_app(post_init_callback=None):
    builder = ApplicationBuilder().token(settings.telegram_bot_token)
    if post_init_callback:
        builder = builder.post_init(post_init_callback)
    app = builder.build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("search", lambda u, c: search_menu_handler(u, c)))
    app.add_handler(CommandHandler("profile", lambda u, c: profile_menu_handler(u, c)))

    # Callback queries — menu navigation
    app.add_handler(CallbackQueryHandler(search_menu_handler, pattern="^menu_search$"))
    app.add_handler(CallbackQueryHandler(my_jobs_handler, pattern="^menu_my_jobs$"))
    app.add_handler(CallbackQueryHandler(profile_menu_handler, pattern="^menu_profile$"))
    app.add_handler(CallbackQueryHandler(stats_handler, pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(start_handler, pattern="^back_main$"))

    # Search presets
    app.add_handler(CallbackQueryHandler(search_preset_handler, pattern="^search_(regional|germany|international|europe)$"))
    app.add_handler(CallbackQueryHandler(custom_search_handler, pattern="^search_custom$"))

    # Job actions
    app.add_handler(CallbackQueryHandler(ai_analysis_handler, pattern=r"^ai_\d+$"))
    app.add_handler(CallbackQueryHandler(save_job_handler, pattern=r"^save_\d+$"))

    # Application status
    app.add_handler(CallbackQueryHandler(status_update_handler, pattern=r"^status_\d+_\w+$"))

    # Profile editing
    app.add_handler(CallbackQueryHandler(profile_field_handler, pattern=r"^prof_\w+$"))

    # Text handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_router))

    return app


async def _text_router(update, context):
    """Route text messages to the appropriate handler based on state."""
    if context.user_data.get("editing_profile_field"):
        await profile_text_handler(update, context)
    elif context.user_data.get("awaiting_custom_search"):
        await text_search_handler(update, context)
