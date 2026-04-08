import logging
import threading

import uvicorn

from app.config import settings


def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, settings.log_level),
    )
    logger = logging.getLogger(__name__)

    # Initialize database (sync wrapper)
    import asyncio
    from app.database import init_db
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    logger.info("Database initialized")

    # Start FastAPI in background thread
    api_thread = threading.Thread(
        target=lambda: uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="warning"),
        daemon=True,
    )
    api_thread.start()
    logger.info("FastAPI started on port 8000")

    # Build bot with post_init that starts scheduler
    from app.bot.bot import create_bot_app
    from app.services.scheduler_service import start_scheduler

    async def on_post_init(app):
        start_scheduler(app)
        logger.info("Scheduler started")

    bot_app = create_bot_app(post_init_callback=on_post_init)
    logger.info("Starting Telegram bot...")
    bot_app.run_polling()


if __name__ == "__main__":
    main()
