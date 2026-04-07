import asyncio
import logging
import threading

import uvicorn

from app.config import settings
from app.database import init_db


async def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, settings.log_level),
    )
    logger = logging.getLogger(__name__)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Start FastAPI in background thread
    api_thread = threading.Thread(
        target=lambda: uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="warning"),
        daemon=True,
    )
    api_thread.start()
    logger.info("FastAPI started on port 8000")

    # Start Telegram bot (blocks)
    from app.bot.bot import create_bot
    from app.services.scheduler_service import start_scheduler

    bot_app = create_bot()
    start_scheduler(bot_app)
    logger.info("Starting Telegram bot...")
    await bot_app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
