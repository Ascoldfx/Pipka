import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api import jobs as jobs_api
from app.api.profile import _parse_country_codes
from app.models import Base
from app.models.application import Application
from app.models.job import Job
from app.models.user import User, UserProfile
from app.scoring.profile_hash import compute_profile_hash


def test_country_codes_are_normalised_and_deduplicated():
    assert _parse_country_codes(" SG, de,sg, AT ", "hidden_countries") == ["sg", "de", "at"]


@pytest.mark.parametrize("value", ["singapore", "s1", "d", "de-de"])
def test_country_codes_reject_invalid_values(value):
    with pytest.raises(HTTPException) as exc_info:
        _parse_country_codes(value, "hidden_countries")
    assert exc_info.value.status_code == 400


def test_hidden_countries_do_not_invalidate_scoring_profile():
    visible = UserProfile(resume_text="Supply chain director", hidden_countries=[])
    hidden = UserProfile(resume_text="Supply chain director", hidden_countries=["sg"])
    assert compute_profile_hash(visible) == compute_profile_hash(hidden)


def test_target_countries_still_invalidate_scoring_profile():
    germany = UserProfile(resume_text="Supply chain director", preferred_countries=["de"])
    singapore = UserProfile(resume_text="Supply chain director", preferred_countries=["sg"])
    assert compute_profile_hash(germany) != compute_profile_hash(singapore)


async def _get_jobs(request, **overrides):
    params = {
        "page": 1,
        "per_page": 50,
        "sort": "date",
        "order": "desc",
        "min_score": 0,
        "source": None,
        "search": None,
        "status": None,
        "region": None,
        "country": None,
        "countries": None,
        "include_closed": 0,
        "semantic": 0,
    }
    params.update(overrides)
    return await jobs_api.get_jobs(request=request, **params)


@pytest.mark.asyncio
async def test_hidden_country_affects_feed_but_not_explicit_filter_or_history(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(id=1, name="Test", profile=UserProfile(hidden_countries=["sg"]))
        germany = Job(
            external_id="de-1",
            source="test",
            title="Head of Logistics",
            country="de",
            dedup_hash="de-1",
        )
        singapore = Job(
            external_id="sg-1",
            source="test",
            title="Head of Logistics",
            country="sg",
            dedup_hash="sg-1",
        )
        session.add_all([user, germany, singapore])
        await session.flush()
        session.add(Application(user_id=user.id, job_id=singapore.id, status="applied"))
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
    assert [job["country"] for job in default_feed["jobs"]] == ["de"]
    assert default_feed["hidden_countries_applied"] == ["sg"]

    explicit_singapore = await _get_jobs(request, country="sg")
    assert [job["country"] for job in explicit_singapore["jobs"]] == ["sg"]
    assert explicit_singapore["hidden_countries_applied"] == []

    applied_history = await _get_jobs(request, status="applied")
    assert [job["country"] for job in applied_history["jobs"]] == ["sg"]
    assert applied_history["hidden_countries_applied"] == []

    await engine.dispose()
