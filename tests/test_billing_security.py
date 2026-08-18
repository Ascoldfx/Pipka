from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.billing import crypto_webhook
from app.api.billing import test_fulfill_checkout as sandbox_fulfill_checkout
from app.config import settings
from app.models.user import User
from app.services.billing_service import sandbox_billing_enabled


def _request_with_body(body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webhooks/crypto",
            "headers": [],
        },
        receive=receive,
    )


def test_sandbox_is_disabled_when_live_credentials_are_present(monkeypatch):
    monkeypatch.setattr(settings, "billing_test_mode", True)
    monkeypatch.setattr(settings, "cryptomus_merchant_id", "merchant")
    monkeypatch.setattr(settings, "cryptomus_payment_key", "secret")

    assert sandbox_billing_enabled() is False


@pytest.mark.asyncio
async def test_unconfigured_webhook_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "cryptomus_merchant_id", "")
    monkeypatch.setattr(settings, "cryptomus_payment_key", "")

    with pytest.raises(HTTPException) as exc_info:
        await crypto_webhook(_request_with_body(b'{"status":"paid"}'), AsyncMock())

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_test_fulfilment_route_is_hidden_outside_sandbox(monkeypatch):
    monkeypatch.setattr(settings, "billing_test_mode", False)
    monkeypatch.setattr(settings, "cryptomus_merchant_id", "")
    monkeypatch.setattr(settings, "cryptomus_payment_key", "")

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_fulfill_checkout("any-id", User(id=1), AsyncMock())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_sandbox_fulfilment_is_scoped_to_current_user(monkeypatch):
    monkeypatch.setattr(settings, "billing_test_mode", True)
    monkeypatch.setattr(settings, "cryptomus_merchant_id", "")
    monkeypatch.setattr(settings, "cryptomus_payment_key", "")
    user = User(id=7)
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.return_value = query_result

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_fulfill_checkout("another-users-id", user, session)

    assert exc_info.value.status_code == 404
    statement = session.execute.await_args.args[0]
    assert "payment_transactions.user_id" in str(statement)
    assert 7 in statement.compile().params.values()
