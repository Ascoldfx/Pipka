from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy.exc import SQLAlchemyError

import app.api.health as health_module
import app.services.scheduler_service as scheduler_module


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _HealthySession:
    def __init__(self, scan_at):
        self.scan_at = scan_at
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return _Result(self.scan_at if self.calls == 2 else 1)


@pytest.mark.asyncio
async def test_health_checks_database_scheduler_and_scan_age(monkeypatch):
    scan_at = datetime.now() - timedelta(minutes=5)

    @asynccontextmanager
    async def fake_session():
        yield _HealthySession(scan_at)

    monkeypatch.setattr(health_module, "async_session", fake_session)
    monkeypatch.setattr(scheduler_module, "scheduler", SimpleNamespace(running=True))
    response = Response()

    payload = await health_module.health(response)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["checks"]["database"] == "ok"
    assert 290 <= payload["checks"]["last_scan_age_seconds"] <= 310


@pytest.mark.asyncio
async def test_health_is_503_when_database_is_unavailable(monkeypatch):
    @asynccontextmanager
    async def failed_session():
        raise SQLAlchemyError("database unavailable")
        yield

    monkeypatch.setattr(health_module, "async_session", failed_session)
    monkeypatch.setattr(scheduler_module, "scheduler", SimpleNamespace(running=True))
    response = Response()

    payload = await health_module.health(response)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["checks"]["database"] == "error"
