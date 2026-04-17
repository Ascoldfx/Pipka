from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Lazy engine — created on first use in the running event loop
_engine = None
_async_session = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory():
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(_get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _async_session


def async_session():
    """Return a new async session context manager."""
    return _get_session_factory()()


async def get_session() -> AsyncSession:
    async with _get_session_factory()() as session:
        yield session


async def init_db():
    from app.models import Base
    from sqlalchemy import text
    import logging

    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Soft migrations — each in its own transaction (PG aborts tx on error)
    migrations = [
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS excluded_keywords JSON",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(320)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user'",
        "ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS english_only BOOLEAN DEFAULT FALSE",
        # Create unique indexes if not exist
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS target_companies JSON",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users(email) WHERE email IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL",
        # NOTE: admin role is assigned via ADMIN_EMAILS env var in user_service.py, not here
    ]
    for sql in migrations:
        try:
            async with _get_engine().begin() as conn:
                await conn.execute(text(sql))
            logging.info("Migration OK: %s", sql[:60])
        except Exception as e:
            logging.debug("Migration skipped (%s): %s", sql[:40], e)
