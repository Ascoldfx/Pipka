import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.bot.handlers.inbox import inbox_handler, inbox_menu_handler
from app.bot.handlers.results import (
    ai_analysis_handler,
    applied_handler,
    reject_handler,
    save_job_handler,
)
from app.bot.handlers.search import (
    custom_search_handler,
    search_menu_handler,
    search_preset_handler,
    show_more_handler,
    text_search_handler,
)
from app.bot.handlers.settings import (
    profile_field_handler,
    profile_menu_handler,
    profile_text_handler,
)
from app.bot.handlers.start import help_handler, start_handler
from app.bot.handlers.tracker import my_jobs_handler, stats_handler, status_update_handler
from app.config import settings
from app.database import async_session
from app.services.user_service import UserAccessDenied, get_or_create_user

logger = logging.getLogger(__name__)


def create_bot_app(post_init_callback=None):
    builder = ApplicationBuilder().token(settings.telegram_bot_token)
    if post_init_callback:
        builder = builder.post_init(post_init_callback)
    app = builder.build()

    # Authorise every update before any command, callback, pagination, or
    # stateful text handler runs. Individual handlers still resolve the user
    # to obtain the ORM object, but this gate closes paths such as pagination
    # that otherwise only read data cached in ``context.user_data``.
    app.add_handler(TypeHandler(Update, _access_guard), group=-1)

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("search", lambda u, c: search_menu_handler(u, c)))
    app.add_handler(CommandHandler("profile", lambda u, c: profile_menu_handler(u, c)))

    # Callback queries — menu navigation
    app.add_handler(CallbackQueryHandler(search_menu_handler, pattern="^menu_search$"))
    app.add_handler(CallbackQueryHandler(inbox_menu_handler, pattern="^menu_inbox$"))
    app.add_handler(CallbackQueryHandler(my_jobs_handler, pattern="^menu_my_jobs$"))
    app.add_handler(CallbackQueryHandler(profile_menu_handler, pattern="^menu_profile$"))
    app.add_handler(CallbackQueryHandler(stats_handler, pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(start_handler, pattern="^back_main$"))

    # Search presets
    app.add_handler(CallbackQueryHandler(search_preset_handler, pattern="^search_(regional|germany|international|europe|cee)$"))
    app.add_handler(CallbackQueryHandler(custom_search_handler, pattern="^search_custom$"))

    # Inbox filters
    app.add_handler(CallbackQueryHandler(inbox_handler, pattern="^inbox_(all|good|top)$"))

    # Pagination
    app.add_handler(CallbackQueryHandler(show_more_handler, pattern="^show_more$"))

    # Job actions
    app.add_handler(CallbackQueryHandler(ai_analysis_handler, pattern=r"^ai_\d+$"))
    app.add_handler(CallbackQueryHandler(save_job_handler, pattern=r"^save_\d+$"))
    app.add_handler(CallbackQueryHandler(applied_handler, pattern=r"^applied_\d+$"))
    app.add_handler(CallbackQueryHandler(reject_handler, pattern=r"^reject_\d+$"))

    # Application status
    app.add_handler(CallbackQueryHandler(status_update_handler, pattern=r"^status_\d+_\w+$"))

    # Profile editing
    app.add_handler(CallbackQueryHandler(profile_field_handler, pattern=r"^prof_\w+$"))

    # Text handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_router))
    app.add_error_handler(_error_handler)

    return app


async def _access_guard(update, context):
    identity = update.effective_user
    if identity is None:
        raise ApplicationHandlerStop
    try:
        async with async_session() as session:
            await get_or_create_user(identity.id, identity.full_name, session)
            await session.commit()
    except UserAccessDenied:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Доступ к Pipka закрыт. Обратитесь к администратору."
            )
        raise ApplicationHandlerStop


async def _text_router(update, context):
    """Route text messages to the appropriate handler based on state."""
    if context.user_data.get("editing_profile_field"):
        await profile_text_handler(update, context)
    elif context.user_data.get("awaiting_custom_search"):
        await text_search_handler(update, context)


async def _error_handler(update, context):
    """Return a quiet denial for closed/inactive accounts.

    Every data-bearing bot handler resolves the Telegram identity through
    ``get_or_create_user``. Keeping the denial in one error hook prevents a
    forgotten handler from accidentally turning access-control failures into
    noisy tracebacks or exposing whether an account was merely inactive.
    """
    error = context.error
    if isinstance(error, UserAccessDenied):
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Доступ к Pipka закрыт. Обратитесь к администратору."
            )
        return
    logger.error(
        "Unhandled Telegram update error: %s",
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )
