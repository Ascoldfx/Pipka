import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.user import User
from app.services.user_service import (
    UserAccessDenied,
    get_or_create_google_user,
    get_or_create_user,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_closed_registration_rejects_unknown_telegram_user(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", False)
    monkeypatch.setattr(settings, "allowed_telegram_ids", "")

    async with session_factory() as session:
        with pytest.raises(UserAccessDenied, match="Registration is closed"):
            await get_or_create_user(123456, "Unknown", session)


@pytest.mark.asyncio
async def test_telegram_allowlist_can_create_user(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", False)
    monkeypatch.setattr(settings, "allowed_telegram_ids", "123456, invalid")

    async with session_factory() as session:
        user = await get_or_create_user(123456, "Invited", session)
        await session.commit()

    assert user.telegram_id == 123456
    assert user.is_active is True


@pytest.mark.asyncio
async def test_inactive_telegram_user_stays_revoked(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", True)
    async with session_factory() as session:
        session.add(User(telegram_id=123456, name="Revoked", is_active=False))
        await session.commit()

        with pytest.raises(UserAccessDenied, match="inactive"):
            await get_or_create_user(123456, "Revoked", session)


@pytest.mark.asyncio
async def test_google_registration_requires_invite_but_allows_admin(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", False)
    monkeypatch.setattr(settings, "allowed_user_emails", "invited@example.com")
    monkeypatch.setattr(settings, "admin_emails", "owner@example.com")

    async with session_factory() as session:
        with pytest.raises(UserAccessDenied, match="Registration is closed"):
            await get_or_create_google_user(
                "outsider-sub", "outsider@example.com", "Outsider", None, session
            )

    async with session_factory() as session:
        invited = await get_or_create_google_user(
            "invited-sub", "invited@example.com", "Invited", None, session
        )
        owner = await get_or_create_google_user(
            "owner-sub", "owner@example.com", "Owner", None, session
        )

    assert invited.role == "user"
    assert owner.role == "admin"


@pytest.mark.asyncio
async def test_inactive_google_user_stays_revoked(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allow_public_registration", True)
    async with session_factory() as session:
        session.add(
            User(
                google_sub="revoked-sub",
                email="revoked@example.com",
                name="Revoked",
                is_active=False,
            )
        )
        await session.commit()

        with pytest.raises(UserAccessDenied, match="inactive"):
            await get_or_create_google_user(
                "revoked-sub", "revoked@example.com", "Revoked", None, session
            )
