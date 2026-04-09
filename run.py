import asyncio
import logging

import uvicorn

from app.config import settings


async def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, settings.log_level),
    )
    logger = logging.getLogger(__name__)

    # Initialize database
    from app.database import init_db
    await init_db()
    logger.info("Database initialized")

    # Start FastAPI server in background
    from app.main import app as fastapi_app
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    logger.info("FastAPI started on port 8000")

    # Build bot
    from app.bot.bot import create_bot_app
    from app.services.scheduler_service import start_scheduler

    bot_app = create_bot_app()
    logger.info("Starting Telegram bot...")

    # Run bot polling (blocks until stopped)
    async with bot_app:
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()

        # Start scheduler AFTER bot is fully running
        start_scheduler(bot_app)
        logger.info("Scheduler started")
        logger.info("Bot is running")

        # Keep running until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
