from fastapi.testclient import TestClient

from app.main import app


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


def test_security_headers_cover_csrf_rejections() -> None:
    client = TestClient(app, base_url="https://localhost")
    client.get("/api/me")

    response = client.post("/auth/logout")

    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
