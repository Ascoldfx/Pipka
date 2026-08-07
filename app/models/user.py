from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin / user
    name: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(5), default="ru")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    subscription_tier: Mapped[str] = mapped_column(String(20), default="free")
    credits: Mapped[int] = mapped_column(Integer, default=50)
    total_credits_purchased: Mapped[int] = mapped_column(Integer, default=0)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    scores: Mapped[list["JobScore"]] = relationship(back_populates="user")
    applications: Mapped[list["Application"]] = relationship(back_populates="user")
    subscriptions: Mapped[list["SearchSubscription"]] = relationship(back_populates="user")
    payment_transactions: Mapped[list["PaymentTransaction"]] = relationship(back_populates="user")
    feedbacks: Mapped[list["UserFeedback"]] = relationship(back_populates="user")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount_usd: Mapped[float] = mapped_column(Float)
    credits_added: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    provider: Mapped[str] = mapped_column(String(30), default="cryptomus")
    provider_tx_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="payment_transactions")


class UserFeedback(Base):
    __tablename__ = "user_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(50), default="general")
    message: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="feedbacks")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    resume_text: Mapped[str | None] = mapped_column(Text)
    target_titles: Mapped[list | None] = mapped_column(JSON)  # ["Supply Chain Manager", "Procurement Lead"]
    max_commute_km: Mapped[int | None] = mapped_column(Integer)
    languages: Mapped[dict | None] = mapped_column(JSON)  # {"en": "C1", "de": "B1"}
    experience_years: Mapped[int | None] = mapped_column(Integer)
    industries: Mapped[list | None] = mapped_column(JSON)
    work_mode: Mapped[str | None] = mapped_column(String(20))  # remote / hybrid / onsite / any
    preferred_countries: Mapped[list | None] = mapped_column(JSON)  # ["de", "ch", "at"]
    hidden_countries: Mapped[list | None] = mapped_column(JSON)  # hidden from default feed; explicit filter overrides
    base_location: Mapped[str | None] = mapped_column(String(255))  # "Leipzig"
    excluded_keywords: Mapped[list | None] = mapped_column(JSON)  # content phrases: ["gambling", "native German"]
    excluded_companies: Mapped[list | None] = mapped_column(JSON)  # exact company matches: ["Amazon", "SAP"]
    english_only: Mapped[bool] = mapped_column(Boolean, default=False)  # prioritise English-language jobs only
    target_companies: Mapped[list | None] = mapped_column(JSON)  # ["Nestlé", "Bayer", "Unilever"]
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="profile")


from app.models.job import JobScore  # noqa: E402
from app.models.application import Application, SearchSubscription  # noqa: E402
