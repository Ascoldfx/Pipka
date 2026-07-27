import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.application import Application
from app.models.job import Job
from app.models.user import User, UserProfile
from app.services.tracker_service import check_auto_exclude_company


@pytest.mark.asyncio
async def test_auto_exclude_uses_dedicated_company_list():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        profile = UserProfile(excluded_keywords=[])
        user = User(id=1, name="Test", profile=profile)
        jobs = [
            Job(
                external_id=f"amazon-{index}",
                source="test",
                title="Head of Supply Chain",
                company_name="Amazon",
                dedup_hash=f"amazon-{index}",
            )
            for index in range(5)
        ]
        session.add_all([user, *jobs])
        await session.flush()
        session.add_all(
            Application(user_id=user.id, job_id=job.id, status="rejected")
            for job in jobs
        )
        await session.commit()

        assert await check_auto_exclude_company(user.id, jobs[0].id, session) == "Amazon"
        await session.refresh(profile)
        assert profile.excluded_companies == ["Amazon"]
        assert profile.excluded_keywords == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_exclude_ignores_nan_company():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        profile = UserProfile(excluded_keywords=[])
        user = User(id=1, name="Test", profile=profile)
        job = Job(
            external_id="nan-company",
            source="test",
            title="Head of Procurement",
            company_name="nan",
            dedup_hash="nan-company",
        )
        session.add_all([user, job])
        await session.commit()

        assert await check_auto_exclude_company(user.id, job.id, session) is None
        await session.refresh(profile)
        assert profile.excluded_companies is None

    await engine.dispose()
