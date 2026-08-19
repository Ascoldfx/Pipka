import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api import jobs as jobs_api
from app.models import Base
from app.models.application import Application
from app.models.job import Job
from app.models.user import User, UserProfile


async def _get_jobs(request: Request, *, status: str | None = None):
    return await jobs_api.get_jobs(
        request=request,
        page=1,
        per_page=50,
        sort="date",
        order="desc",
        min_score=0,
        source=None,
        search=None,
        status=status,
        region=None,
        country=None,
        countries=None,
        include_closed=0,
        semantic=0,
    )


@pytest.mark.asyncio
async def test_rejected_provider_identity_cannot_resurface_as_new_job(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(id=1, name="Test", profile=UserProfile())
        rejected = Job(
            external_id="provider-42",
            source="indeed",
            title="Head of Supply Chain",
            company_name="Example",
            location="Berlin",
            country="de",
            url="https://example.com/jobs/42?old=1",
            dedup_hash="rejected-row",
        )
        replacement = Job(
            external_id="provider-42",
            source="indeed",
            title="Head of Supply Chain (m/f/d)",
            company_name="Example",
            location="Berlin or remote",
            country="de",
            url="https://example.com/jobs/42?new=1",
            dedup_hash="replacement-row",
        )
        distinct_opening = Job(
            external_id="provider-43",
            source="indeed",
            title="Head of Supply Chain",
            company_name="Example",
            location="Munich",
            country="de",
            url="https://example.com/jobs/43",
            dedup_hash="distinct-row",
        )
        session.add_all([user, rejected, replacement, distinct_opening])
        await session.flush()
        session.add(Application(user_id=user.id, job_id=rejected.id, status="rejected"))
        await session.commit()

    monkeypatch.setattr(jobs_api, "async_session", factory)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jobs",
            "headers": [],
            "query_string": b"",
            "session": {"user_id": 1},
        }
    )

    default_feed = await _get_jobs(request)
    assert [job["id"] for job in default_feed["jobs"]] == [distinct_opening.id]

    inbox = await _get_jobs(request, status="new")
    assert [job["id"] for job in inbox["jobs"]] == [distinct_opening.id]

    rejected_history = await _get_jobs(request, status="rejected")
    assert [job["id"] for job in rejected_history["jobs"]] == [rejected.id]

    await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_exact_url_is_hidden_across_sources(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(id=1, name="Test", profile=UserProfile())
        rejected = Job(
            external_id="source-a-1",
            source="jooble",
            title="Operations Director",
            country="de",
            url="https://example.com/jobs/shared",
            dedup_hash="url-rejected",
        )
        replacement = Job(
            external_id="source-b-9",
            source="indeed",
            title="Operations Director",
            country="de",
            url="https://example.com/jobs/shared",
            dedup_hash="url-replacement",
        )
        session.add_all([user, rejected, replacement])
        await session.flush()
        session.add(Application(user_id=user.id, job_id=rejected.id, status="rejected"))
        await session.commit()

    monkeypatch.setattr(jobs_api, "async_session", factory)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jobs",
            "headers": [],
            "query_string": b"",
            "session": {"user_id": 1},
        }
    )

    assert (await _get_jobs(request))["jobs"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_jobs_response_tolerates_legacy_non_object_raw_data(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(id=1, name="Test", profile=UserProfile())
        legacy = Job(
            external_id="legacy-raw-data",
            source="indeed",
            title="Supply Chain Director",
            country="de",
            url="https://example.com/jobs/legacy",
            dedup_hash="legacy-raw-data",
            raw_data="legacy-string",
        )
        session.add_all([user, legacy])
        await session.commit()

    monkeypatch.setattr(jobs_api, "async_session", factory)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jobs",
            "headers": [],
            "query_string": b"",
            "session": {"user_id": 1},
        }
    )

    response = await _get_jobs(request)
    assert response["jobs"][0]["merged_sources"] is None

    await engine.dispose()
