import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api import admin as admin_api
from app.models import Base
from app.models.user import User, UserProfile


@pytest.fixture
async def admin_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def allow_admin(_request):
        return None

    events = []

    async def capture_event(event_type, status, **kwargs):
        events.append((event_type, status, kwargs))

    monkeypatch.setattr(admin_api, "async_session", factory)
    monkeypatch.setattr(admin_api, "require_admin_async", allow_admin)
    monkeypatch.setattr(admin_api, "record_ops_event", capture_event)
    yield factory, events
    await engine.dispose()


def _request(user_id: int) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/admin/user/1/profile",
            "headers": [],
            "query_string": b"",
            "session": {"user_id": user_id, "user_role": "admin"},
        }
    )


@pytest.mark.asyncio
async def test_admin_profile_returns_only_bounded_resume_preview(admin_store):
    factory, events = admin_store
    async with factory() as session:
        user = User(email="user@example.com", name="User")
        session.add(user)
        await session.flush()
        session.add(UserProfile(user_id=user.id, resume_text="x" * 2000))
        await session.commit()
        user_id = user.id

    response = await admin_api.admin_get_user_profile(_request(99), user_id)

    profile = response["profile"]
    assert "resume_text" not in profile
    assert profile["resume_preview"] == "x" * 1500
    assert profile["resume_truncated"] is True
    assert events[-1][0:2] == ("admin_action", "success")
    assert events[-1][2]["payload"]["target_user_id"] == user_id


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self_or_another_admin(admin_store):
    factory, events = admin_store
    async with factory() as session:
        actor = User(email="owner@example.com", role="admin", name="Owner")
        peer = User(email="peer@example.com", role="admin", name="Peer")
        session.add_all([actor, peer])
        await session.commit()
        actor_id, peer_id = actor.id, peer.id

    with pytest.raises(HTTPException) as self_error:
        await admin_api.admin_delete_user(_request(actor_id), actor_id)
    assert self_error.value.status_code == 409

    with pytest.raises(HTTPException) as peer_error:
        await admin_api.admin_delete_user(_request(actor_id), peer_id)
    assert peer_error.value.status_code == 409
    assert [status for _, status, _ in events] == ["denied", "denied"]


@pytest.mark.asyncio
async def test_admin_deactivation_is_soft_delete_and_audited(admin_store):
    factory, events = admin_store
    async with factory() as session:
        user = User(email="target@example.com", name="Target")
        session.add(user)
        await session.commit()
        user_id = user.id

    response = await admin_api.admin_delete_user(_request(99), user_id)

    assert response == {"ok": True}
    async with factory() as session:
        target = await session.get(User, user_id)
        assert target is not None
        assert target.is_active is False
    assert events[-1][0:2] == ("admin_action", "success")
