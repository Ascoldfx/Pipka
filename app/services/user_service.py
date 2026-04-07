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
