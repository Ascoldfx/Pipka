from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User, UserProfile


class UserAccessDenied(PermissionError):
    """The identity is inactive or is not allowed to register."""


def _csv_values(raw: str) -> set[str]:
    return {value.strip().casefold() for value in raw.split(",") if value.strip()}


def _allowed_telegram_ids() -> set[int]:
    from app.config import settings

    result: set[int] = set()
    for value in settings.allowed_telegram_ids.split(","):
        value = value.strip()
        if value and value.lstrip("-").isdigit():
            result.add(int(value))
    return result


async def get_or_create_user(telegram_id: int, name: str | None, session: AsyncSession) -> User:
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is not None and not user.is_active:
        raise UserAccessDenied("Account is inactive")
    if user is None:
        from app.config import settings

        if not settings.allow_public_registration and telegram_id not in _allowed_telegram_ids():
            raise UserAccessDenied("Registration is closed")
        user = User(telegram_id=telegram_id, name=name)
        session.add(user)
        await session.flush()
    return user


async def get_or_create_google_user(
    google_sub: str, email: str, name: str | None, avatar_url: str | None, session: AsyncSession
) -> User:
    """Find user by google_sub or email, or create a new one."""
    from app.config import settings

    # 1) Try by google_sub
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.google_sub == google_sub)
    )
    user = result.scalar_one_or_none()

    # 2) Try by email (may exist from Telegram with same email)
    if user is None:
        result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        if user:
            user.google_sub = google_sub  # link Google identity

    if user is not None and not user.is_active:
        raise UserAccessDenied("Account is inactive")

    admin_emails = _csv_values(settings.admin_emails)

    # 3) Create new user only when registration policy permits it.
    if user is None:
        normalised_email = email.casefold()
        invited_emails = _csv_values(settings.allowed_user_emails)
        if (
            not settings.allow_public_registration
            and normalised_email not in admin_emails
            and normalised_email not in invited_emails
        ):
            raise UserAccessDenied("Registration is closed")
        role = "admin" if normalised_email in admin_emails else "user"
        user = User(
            google_sub=google_sub,
            email=email,
            name=name,
            avatar_url=avatar_url,
            role=role,
        )
        session.add(user)
        await session.flush()

    # Update avatar/name if changed
    if avatar_url and user.avatar_url != avatar_url:
        user.avatar_url = avatar_url
    if name and not user.name:
        user.name = name

    await session.commit()
    return user


async def ensure_profile(user: User, session: AsyncSession) -> UserProfile:
    if user.profile:
        return user.profile
    profile = UserProfile(user_id=user.id)
    session.add(profile)
    await session.flush()
    user.profile = profile
    return profile


async def update_profile(user: User, session: AsyncSession, **kwargs) -> UserProfile:
    profile = await ensure_profile(user, session)
    for key, value in kwargs.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    await session.commit()
    return profile
