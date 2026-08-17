import pytest
from fastapi import HTTPException, Request

from app.api import ops as ops_api


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "path"),
    [
        (ops_api.get_ops_overview, "/api/ops/overview"),
        (ops_api.get_dedup_jobs, "/api/ops/dedup"),
    ],
)
async def test_ops_user_queues_are_denied_before_any_data_query(
    monkeypatch, endpoint, path
):
    async def deny_admin(_request):
        raise HTTPException(status_code=403, detail="Admin access required")

    monkeypatch.setattr(ops_api, "require_admin_async", deny_admin)

    with pytest.raises(HTTPException) as exc:
        await endpoint(_request(path))

    assert exc.value.status_code == 403
