from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User, UserProfile


async def get_or_create_user(telegram_id: int, name: str | None, session: AsyncSession) -> User:
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
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

    # 3) Create new user
    if user is None:
        admin_emails = [e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()]
        role = "admin" if email.lower() in admin_emails else "user"
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
