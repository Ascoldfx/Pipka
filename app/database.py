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
        
        # Soft migration: Add excluded_keywords if it doesn't exist
        try:
            await conn.execute(text("ALTER TABLE user_profiles ADD COLUMN excluded_keywords JSON"))
            logging.info("Migrated user_profiles: added excluded_keywords")
        except Exception:
            # Column likely already exists
            pass
