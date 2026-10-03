"""urlscan·콜백 요청이 공유 httpx 클라이언트를 쓰는지 검증한다."""

import httpx
import pytest

import backend.src.server.urlscan_service as service


@pytest.fixture
def shared_client(monkeypatch):
    """네트워크 대신 MockTransport 로 응답하고, 새 클라이언트 생성은 막는다."""
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"uuid": "scan-1"})
        if request.url.path.endswith("/pending/"):
            return httpx.Response(404)
        return httpx.Response(200, json={"page": {"url": "https://example.com"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(service, "_client", client)
    monkeypatch.setattr(service, "URLSCAN_API_KEY", "test-key")

    def forbidden(*args, **kwargs):
        raise AssertionError("요청마다 httpx.AsyncClient 를 새로 만들면 안 된다")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    return requests


@pytest.mark.asyncio
async def test_submit_and_poll_reuse_shared_client(shared_client):
    assert (await service.submit_url_scan("https://example.com"))["uuid"] == "scan-1"
    assert await service.get_url_scan_result("done") == {"page": {"url": "https://example.com"}}
    assert await service.get_url_scan_result("pending") is None
    assert len(shared_client) == 3
    assert all(r.headers["API-Key"] == "test-key" for r in shared_client)


@pytest.mark.asyncio
async def test_lifespan_owns_client_lifecycle():
    with pytest.raises(RuntimeError):
        service.get_http_client()

    async with service.http_client_lifespan(None):
        client = service.get_http_client()
        assert not client.is_closed

    assert client.is_closed
    with pytest.raises(RuntimeError):
        service.get_http_client()
