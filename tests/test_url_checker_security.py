import httpx
import pytest

from app.services import url_checker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/internal",
        "http://[::1]/admin",
        "https://example.com:8443/job",
        "https://user:pass@example.com/job",
    ],
)
async def test_validate_public_url_rejects_ssrf_targets(url: str) -> None:
    with pytest.raises(url_checker.UnsafeURL):
        await url_checker._validate_public_url(url)


@pytest.mark.asyncio
async def test_validate_public_url_rejects_domain_resolving_private(monkeypatch) -> None:
    original_resolver = url_checker._resolve_host_addresses

    async def private_addresses(host: str, port: int):
        return await original_resolver("10.0.0.8", port)

    monkeypatch.setattr(url_checker, "_resolve_host_addresses", private_addresses)

    with pytest.raises(url_checker.UnsafeURL):
        await url_checker._validate_public_url("https://jobs.example/internal")


@pytest.mark.asyncio
async def test_fetch_body_revalidates_redirect_before_following(monkeypatch) -> None:
    requests: list[str] = []
    original_resolver = url_checker._resolve_host_addresses

    async def public_addresses(host: str, port: int):
        if host == "jobs.example":
            return await original_resolver("93.184.216.34", port)
        return await original_resolver(host, port)

    async def no_pacing(host: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    monkeypatch.setattr(url_checker, "_resolve_host_addresses", public_addresses)
    monkeypatch.setattr(url_checker, "_pace_host", no_pacing)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        with pytest.raises(url_checker.UnsafeURL):
            await url_checker._fetch_public_body("https://jobs.example/role/123", client)

    assert requests == ["https://jobs.example/role/123"]
