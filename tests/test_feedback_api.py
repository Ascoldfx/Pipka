import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import feedback as feedback_api
from app.config import settings
from app.models import Base
from app.models.user import User, UserFeedback


@pytest.mark.asyncio
async def test_feedback_is_normalized_and_persisted(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings, "allowed_telegram_ids", "")
    async with factory() as session:
        user = User(id=1, name="Test User", email="test@example.com")
        session.add(user)
        await session.commit()

        result = await feedback_api.submit_feedback(
            feedback_api.FeedbackRequest(
                category=" Bug ",
                message="The feedback button needs attention.",
                contact="@test",
            ),
            current_user=user,
            session=session,
        )

        stored = (await session.execute(select(UserFeedback))).scalar_one()
        assert result["ok"] is True
        assert stored.user_id == user.id
        assert stored.category == "bug"
        assert stored.message == "The feedback button needs attention."

    await engine.dispose()


@pytest.mark.asyncio
async def test_feedback_rejects_unknown_category():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(id=1, name="Test User")
        session.add(user)
        await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await feedback_api.submit_feedback(
                feedback_api.FeedbackRequest(category="unknown", message="Valid length"),
                current_user=user,
                session=session,
            )

        assert exc_info.value.status_code == 422

    await engine.dispose()
