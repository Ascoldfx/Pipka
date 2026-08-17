from app.models.user import User, UserProfile
from app.services.user_scope_service import (
    build_user_search_plans,
    merge_user_search_plans,
    profile_target_countries,
)


def _user(user_id: int, titles: list[str], countries: list[str]) -> User:
    return User(
        id=user_id,
        profile=UserProfile(target_titles=titles, preferred_countries=countries),
    )


def test_search_plans_keep_roles_bound_to_each_users_countries():
    users = [
        _user(1, ["Director Supply Chain", "Head of Procurement"], ["de"]),
        _user(2, ["AI Strategy Director", "Chief of Staff AI"], ["sg"]),
    ]

    params = merge_user_search_plans(build_user_search_plans(users))

    assert params is not None
    assert params.queries == [
        "Director Supply Chain",
        "AI Strategy Director",
        "Head of Procurement",
        "Chief of Staff AI",
    ]
    assert params.countries == ["de", "sg"]
    assert params.queries_for_country("de") == [
        "Director Supply Chain",
        "Head of Procurement",
    ]
    assert params.queries_for_country("sg") == [
        "AI Strategy Director",
        "Chief of Staff AI",
    ]


def test_search_plan_round_robin_rotates_first_user_and_interleaves_shared_market():
    users = [
        _user(1, ["Role A1", "Role A2"], ["de"]),
        _user(2, ["Role B1", "Role B2"], ["de"]),
    ]

    params = merge_user_search_plans(
        build_user_search_plans(users), start_offset=1
    )

    assert params is not None
    assert params.queries == ["Role B1", "Role A1", "Role B2", "Role A2"]
    assert params.queries_for_country("de") == params.queries


def test_incomplete_profile_does_not_fall_back_to_germany():
    user = _user(1, ["Director Supply Chain"], [])

    assert profile_target_countries(user.profile) == ()
    assert build_user_search_plans([user]) == []
    assert merge_user_search_plans([]) is None
