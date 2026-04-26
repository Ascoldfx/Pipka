from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Use JSONB on PostgreSQL (faster operators, GIN-indexable) but keep JSON for
# sqlite (used in dev/tests) — SQLAlchemy variant picks the right one per dialect.
_JSON = JSON().with_variant(JSONB(), "postgresql")

from app.models import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # Dashboard listing sorts by posted_at desc and filters by source / country.
        Index("ix_jobs_posted_at_desc", "posted_at"),
        Index("ix_jobs_source", "source"),
        Index("ix_jobs_country", "country"),
        # Hot path: backfill scorer + cleanup walk all jobs newer than N days.
        Index("ix_jobs_scraped_at", "scraped_at"),
        # NVIDIA idle rescorer + dashboard country filters scope by country
        # then by recency in the same WHERE clause.
        Index("ix_jobs_country_scraped", "country", "scraped_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(50))  # adzuna / linkedin / indeed / google / arbeitsagentur
    title: Mapped[str] = mapped_column(String(500))
    company_name: Mapped[str | None] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(10))
    description: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(10))
    url: Mapped[str | None] = mapped_column(Text)
    is_remote: Mapped[bool | None] = mapped_column(default=None)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    raw_data: Mapped[dict | None] = mapped_column(_JSON)
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    scores: Mapped[list["JobScore"]] = relationship(back_populates="job")


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id"),
        # Hot paths: LEFT JOIN scores on (job_id, user_id) in get_jobs + per-user aggregates in get_stats.
        Index("ix_job_scores_user_job", "user_id", "job_id"),
        Index("ix_job_scores_user_score", "user_id", "score"),
        # NVIDIA idle rescorer (priority b) walks stale successful scores
        # ordered by scored_at — index lets us skip the seq scan.
        Index("ix_job_scores_user_scored_at", "user_id", "scored_at"),
        # Phase 2 cache lookups: "is this (job, profile, model) tuple already
        # scored?" Compound covers both equality probes and invalidation scans.
        Index("ix_job_scores_user_profile_model", "user_id", "profile_hash", "model_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    score: Mapped[int] = mapped_column(Integer)  # 0-100
    ai_analysis: Mapped[str | None] = mapped_column(Text)
    breakdown: Mapped[dict | None] = mapped_column(JSON)  # {"relevance": 85, "language_fit": 70, ...}
    scored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Phase 2 — fingerprints for cache invalidation.
    # profile_hash: sha256 of stable JSON of scoring-relevant profile fields.
    # model_version: e.g. "gemini-3.1-flash-lite-preview" or "claude-sonnet-4-20250514".
    # Both nullable for legacy rows scored before the migration.
    profile_hash: Mapped[str | None] = mapped_column(String(64), index=False)
    model_version: Mapped[str | None] = mapped_column(String(64), index=False)

    job: Mapped["Job"] = relationship(back_populates="scores")
    user: Mapped["User"] = relationship(back_populates="scores")


from app.models.user import User  # noqa: E402
