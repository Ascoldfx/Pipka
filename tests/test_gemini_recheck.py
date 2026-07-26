import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.job import Job, JobScore
from app.models.user import User, UserProfile
from app.scoring import gemini_matcher


@pytest.mark.asyncio
async def test_zero_score_recheck_cannot_override_current_profile_rules(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        profile = UserProfile(
            target_titles=["Director Operations"],
            preferred_countries=["qa"],
            english_only=True,
        )
        user = User(id=1, name="Test", profile=profile)
        job = Job(
            external_id="qa-commercial",
            source="indeed",
            title="Director of Retail and Commercial Operations",
            description="English role leading retail sales and store operations.",
            country="qa",
            dedup_hash="qa-commercial",
        )
        session.add_all([user, job])
        await session.flush()
        score = JobScore(
            job_id=job.id,
            user_id=user.id,
            score=0,
            ai_analysis=None,
            model_version="prefilter",
        )
        session.add(score)
        await session.commit()

        async def unexpected_ai_call(*_args, **_kwargs):
            pytest.fail("A current hard reject must not be sent back to Gemini")

        monkeypatch.setattr(gemini_matcher, "_call_gemini_raw", unexpected_ai_call)

        assert await gemini_matcher.recheck_zero_scores(user, session) == (0, 0)
        await session.refresh(score)
        assert score.score == 0
        assert score.model_version == "prefilter"
        assert score.ai_analysis == "Filtered by current profile rules"

    await engine.dispose()
