"""Shared multi-user search and queue scoping.

Jobs are globally de-duplicated, while search intent and scoring state remain
profile-specific.  Keeping the scope helpers in one module prevents the scan,
backfill, embedding worker, and Ops dashboard from silently drifting apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.models.user import User, UserProfile
from app.sources.base import SearchParams
from app.sources.country_queries import expand_queries_for_country


def _normalise_values(values: Iterable[object] | None, *, country: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = str(raw).strip()
        value = value.casefold() if country else " ".join(value.split())
        key = value.casefold()
        if not value or key in seen:
            continue
        if country and (len(value) != 2 or not value.isalpha()):
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)


def profile_target_countries(profile: UserProfile | None) -> tuple[str, ...]:
    """Return only explicit ISO country codes from a user's profile.

    There is deliberately no implicit Germany fallback: an incomplete profile
    must not consume another user's search or scoring capacity.
    """

    return _normalise_values(
        getattr(profile, "preferred_countries", None), country=True
    )


@dataclass(frozen=True)
class UserSearchPlan:
    user_id: int
    queries: tuple[str, ...]
    countries: tuple[str, ...]


@dataclass(frozen=True)
class ActiveTargetScope:
    user_ids: tuple[int, ...]
    countries: tuple[str, ...]


def build_user_search_plans(users: Sequence[User]) -> list[UserSearchPlan]:
    """Build one complete search plan per active, configured user."""

    plans: list[UserSearchPlan] = []
    for user in sorted(users, key=lambda item: item.id):
        profile = user.profile
        queries = _normalise_values(getattr(profile, "target_titles", None))
        countries = profile_target_countries(profile)
        if not queries or not countries:
            continue
        plans.append(UserSearchPlan(user.id, queries, countries))
    return plans


def rotate_items(items: Sequence, offset: int) -> list:
    if not items:
        return []
    start = offset % len(items)
    return list(items[start:]) + list(items[:start])


def _round_robin_values(sequences: Sequence[Sequence[str]]) -> list[str]:
    """Interleave ordered lists and de-duplicate without favouring list one."""

    result: list[str] = []
    seen: set[str] = set()
    max_length = max((len(values) for values in sequences), default=0)
    for index in range(max_length):
        for values in sequences:
            if index >= len(values):
                continue
            value = values[index]
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
    return result


def merge_user_search_plans(
    plans: Sequence[UserSearchPlan], *, start_offset: int = 0
) -> SearchParams | None:
    """Merge plans fairly while retaining exact country/title associations.

    Providers still receive one shared request cycle, preserving their quotas
    and the global vacancy deduplication.  Sources that support country-specific
    queries never search a user's roles in another user's countries.
    """

    rotated = rotate_items(plans, start_offset)
    if not rotated:
        return None

    queries = _round_robin_values([plan.queries for plan in rotated])
    countries = _round_robin_values([plan.countries for plan in rotated])
    country_queries: dict[str, list[str]] = {}
    for country in countries:
        per_user = [
            tuple(expand_queries_for_country(list(plan.queries), country))
            for plan in rotated
            if country in plan.countries
        ]
        country_queries[country] = _round_robin_values(per_user)

    return SearchParams(
        queries=queries,
        countries=countries,
        locations=[],
        country_queries=country_queries,
    )


async def active_target_scope(session) -> ActiveTargetScope:
    """Return active profile ids and the union of their explicit countries."""

    rows = await session.execute(
        select(User.id, UserProfile.preferred_countries)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(User.is_active.is_(True))
        .order_by(User.id)
    )
    active_rows = list(rows.all())
    countries = _round_robin_values([
        _normalise_values(preferred_countries, country=True)
        for _, preferred_countries in active_rows
    ])
    return ActiveTargetScope(
        user_ids=tuple(user_id for user_id, _ in active_rows),
        countries=tuple(countries),
    )
