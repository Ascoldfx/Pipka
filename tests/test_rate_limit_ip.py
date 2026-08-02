from starlette.requests import Request

from app.api import _ratelimit
from app.api._ratelimit import _client_ip, get_user_rate_limit_retry_after


def _request(peer: str, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/jobs",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": (peer, 12345),
            "server": ("localhost", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_loopback_proxy_uses_only_sanitised_real_ip() -> None:
    request = _request(
        "127.0.0.1",
        **{
            "x-real-ip": "203.0.113.42",
            "cf-connecting-ip": "198.51.100.9",
            "x-forwarded-for": "192.0.2.1, 203.0.113.42",
        },
    )

    assert _client_ip(request) == "203.0.113.42"


def test_invalid_forwarded_value_falls_back_to_direct_peer() -> None:
    request = _request("127.0.0.1", **{"x-real-ip": "attacker-controlled-token"})

    assert _client_ip(request) == "127.0.0.1"


def test_non_proxy_peer_cannot_spoof_forwarded_headers() -> None:
    request = _request("198.51.100.20", **{"x-real-ip": "203.0.113.42"})

    assert _client_ip(request) == "198.51.100.20"


def test_bot_and_web_can_share_bounded_user_quota() -> None:
    _ratelimit._buckets.clear()

    assert get_user_rate_limit_retry_after(
        user_id=42, key="telegram_search", limit=2, window_s=3600
    ) is None
    assert get_user_rate_limit_retry_after(
        user_id=42, key="telegram_search", limit=2, window_s=3600
    ) is None
    assert get_user_rate_limit_retry_after(
        user_id=42, key="telegram_search", limit=2, window_s=3600
    ) is not None

    _ratelimit._buckets.clear()
