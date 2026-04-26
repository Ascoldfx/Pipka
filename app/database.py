from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Lazy engine — created on first use in the running event loop
_engine = None
_async_session = None


def _get_engine():
    global _engine
    if _engine is None:
        # Per-statement guards so a runaway query (e.g. ``ILIKE '%foo%'`` over
        # the full jobs table) can't pin a connection forever and starve the
        # pool. Tuned conservatively — most legit queries finish in <100ms.
        connect_args: dict = {}
        if settings.database_url.startswith("postgresql"):
            connect_args["server_settings"] = {
                "statement_timeout": "10000",                  # 10s per query
                "lock_timeout": "3000",                        # 3s waiting on locks
                "idle_in_transaction_session_timeout": "60000",  # 60s idle txn
                "application_name": "pipka",
            }
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            connect_args=connect_args,
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

    # База данных будет управляться через мануальные миграции Alembic
