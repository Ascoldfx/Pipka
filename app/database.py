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
            # 30s per statement bounds runaway queries; 5s lock_timeout fails
            # fast on contended writes. We deliberately do NOT set
            # idle_in_transaction_session_timeout — the scanner holds an open
            # transaction during the multi-minute scrape phase, and killing
            # idle txs there would (and did) terminate live connections.
            connect_args["server_settings"] = {
                "statement_timeout": "30000",
                "lock_timeout": "5000",
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
    """Bring the database schema to ``head`` via Alembic.

    Replaces the legacy ``Base.metadata.create_all()`` call. On a fresh
    database, runs every migration from baseline forward. On an
    already-provisioned production DB, the baseline migration is itself
    idempotent (calls ``create_all`` against existing tables = no-op),
    and subsequent migrations apply normally.

    Alembic uses sync engines, so we run it inside ``run_in_executor`` to
    avoid blocking the event loop during startup.
    """
    import asyncio
    import logging
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    logger = logging.getLogger(__name__)

    def _run_alembic_upgrade() -> None:
        # alembic.ini sits at the repo root; resolve relative to this file.
        ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
        cfg = Config(str(ini_path))
        # Override the sqlalchemy.url so Alembic uses the same DB as the app.
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(cfg, "head")

    try:
        await asyncio.get_running_loop().run_in_executor(None, _run_alembic_upgrade)
        logger.info("Alembic migrations applied (head)")
    except Exception:
        logger.exception("Alembic upgrade failed during init_db")
        raise
