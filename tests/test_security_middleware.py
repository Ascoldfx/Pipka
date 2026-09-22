import re
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import _scrub, app
from app.security_probes import is_known_security_probe


def test_api_schema_is_not_public_by_default() -> None:
    client = TestClient(app, base_url="https://localhost")
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_csrf_rejects_missing_and_invalid_tokens() -> None:
    client = TestClient(app, base_url="https://localhost")

    bootstrap = client.get("/api/me")
    assert bootstrap.status_code == 200
    token = bootstrap.json()["csrf_token"]
    assert token
    assert client.cookies.get("csrf_token") == token

    missing = client.post("/auth/logout")
    assert missing.status_code == 403
    assert missing.json() == {"detail": "CSRF token missing or invalid"}

    invalid = client.post("/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
    assert invalid.status_code == 403


def test_csrf_accepts_matching_session_token() -> None:
    client = TestClient(app, base_url="https://localhost")

    bootstrap = client.get("/api/me")
    token = bootstrap.json()["csrf_token"]

    response = client.post("/auth/logout", headers={"X-CSRF-Token": token})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_probe_does_not_create_session_or_csrf_cookies() -> None:
    client = TestClient(app, base_url="https://localhost")

    response = client.get("/health/live")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_security_headers_cover_csrf_rejections() -> None:
    client = TestClient(app, base_url="https://localhost")
    client.get("/api/me")

    response = client.post("/auth/logout")

    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"


def test_known_framework_probes_are_distinct_from_real_api_errors() -> None:
    assert is_known_security_probe("/api/templates/preview", "POST", 403)
    assert is_known_security_probe("/api/graphql", "POST", 404)
    assert not is_known_security_probe("/api/profile", "POST", 403)
    assert not is_known_security_probe("/api/templates/preview", "GET", 403)
    assert not is_known_security_probe("/api/templates/preview", "POST", 500)


def test_known_probe_is_rejected_without_creating_ops_error(monkeypatch) -> None:
    recorder = AsyncMock()
    monkeypatch.setattr("app.main.record_ops_event", recorder)
    client = TestClient(app, base_url="https://localhost")
    client.get("/api/me")

    response = client.post("/api/templates/preview")

    assert response.status_code == 403
    recorder.assert_not_awaited()


def test_real_api_csrf_error_is_still_recorded(monkeypatch) -> None:
    recorder = AsyncMock()
    monkeypatch.setattr("app.main.record_ops_event", recorder)
    client = TestClient(app, base_url="https://localhost")
    client.get("/api/me")

    response = client.post("/api/profile")

    assert response.status_code == 403
    recorder.assert_awaited_once()


def test_html_pages_nonce_only_their_known_inline_scripts() -> None:
    client = TestClient(app, base_url="https://localhost")

    for path in ("/", "/infographic"):
        response = client.get(path)
        csp = response.headers["content-security-policy"]
        nonce_match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)

        assert response.status_code == 200
        assert nonce_match
        assert "script-src-attr 'none'" in csp
        assert "script-src 'self' 'unsafe-inline'" not in csp
        assert f'nonce="{nonce_match.group(1)}"' in response.text


def test_non_html_responses_do_not_allow_inline_scripts() -> None:
    client = TestClient(app, base_url="https://localhost")

    response = client.get("/health/live")
    csp = response.headers["content-security-policy"]

    assert "script-src 'self';" in csp
    assert "script-src-attr 'none'" in csp
    assert "nonce-" not in csp


def test_sentry_scrubber_is_case_insensitive_and_redacts_secret_shapes() -> None:
    scrubbed = _scrub(
        {
            "authorization": "Bearer secret-value",
            "COOKIE": "pipka_session=signed-value",
            "nested": {
                "Google-Client-Secret": "client-secret",
                "message": "contact person@example.com or use 123456789:abcdefghijklmnopqrstuvwxyz_123456",  # secret-scan: allow
            },
        }
    )

    assert scrubbed["authorization"] == "[redacted]"
    assert scrubbed["COOKIE"] == "[redacted]"
    assert scrubbed["nested"]["Google-Client-Secret"] == "[redacted]"
    assert "person@example.com" not in scrubbed["nested"]["message"]
    assert "123456789:" not in scrubbed["nested"]["message"]
