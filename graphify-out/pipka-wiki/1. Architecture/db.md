# Database Domain

This node describes the Database connectivity strategy and schema management.
Dependencies: [[models.md]], [[config.md]]

## Core Connectivity
File: `app/database.py`
The project leverages `SQLAlchemy 2.0` with standard Async IO using `asyncpg` for PostgreSQL and `aiosqlite` for SQLite.

```python
async_engine = create_async_engine(settings.database_url, echo=settings.db_echo)
async_session = async_sessionmaker(async_engine, expire_on_commit=False)
```

## Schema Migrations (Alembic)
Location: `alembic/versions/`
Changes to any models defined in [[models.md]] must be tracked with Alembic using `alembic revision --autogenerate`.

## Session Usage
Usage within handlers and routes (see [[routes.md]] and [[bot.md]]) typically initializes a transient session using:
```python
async with async_session() as session:
    await session.execute(...)
```
