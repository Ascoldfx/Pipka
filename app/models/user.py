from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(5), default="ru")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    subscription_tier: Mapped[str] = mapped_column(String(20), default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    scores: Mapped[list["JobScore"]] = relationship(back_populates="user")
    applications: Mapped[list["Application"]] = relationship(back_populates="user")
    subscriptions: Mapped[list["SearchSubscription"]] = relationship(back_populates="user")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    resume_text: Mapped[str | None] = mapped_column(Text)
    target_titles: Mapped[list | None] = mapped_column(JSON)  # ["Supply Chain Manager", "Procurement Lead"]
    min_salary: Mapped[int | None] = mapped_column(Integer)
    max_commute_km: Mapped[int | None] = mapped_column(Integer)
    languages: Mapped[dict | None] = mapped_column(JSON)  # {"en": "C1", "de": "B1"}
    experience_years: Mapped[int | None] = mapped_column(Integer)
    industries: Mapped[list | None] = mapped_column(JSON)
    work_mode: Mapped[str | None] = mapped_column(String(20))  # remote / hybrid / onsite / any
    preferred_countries: Mapped[list | None] = mapped_column(JSON)  # ["de", "ch", "at"]
    base_location: Mapped[str | None] = mapped_column(String(255))  # "Leipzig"
    excluded_keywords: Mapped[list | None] = mapped_column(JSON)  # ["junior", "sales", "b2"]
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="profile")


from app.models.job import JobScore  # noqa: E402
from app.models.application import Application, SearchSubscription  # noqa: E402
