"""Callback requests reuse the serving loop's shared client and close on shutdown."""

import httpx
import pytest

import main
from backend.src.server import http_client as service


async def test_callbacks_reuse_shared_client(monkeypatch):
    requests = []
    constructions = []
    original = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "SUCCESS"})

    def client_factory(**kwargs):
        client = original(transport=httpx.MockTransport(handler), **kwargs)
        constructions.append(client)
        return client

    async def analyze(*args, **kwargs):
        return main.kakao_response("완료")

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(main, "run_analysis", analyze)
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    async with service.http_client_lifespan(None):
        for user in ("a", "b"):
            job_id = main.create_analysis_job(user)
            await main.run_analysis_and_callback([], "", "https://bot-api.kakao.com/cb", user, job_id)
            assert main.ANALYSIS_JOBS[job_id]["callback_status"] == "success"
        assert len(constructions) == 1
        assert not constructions[0].is_closed
    assert len(requests) == 2
    assert constructions[0].is_closed
    with pytest.raises(RuntimeError):
        service.get_http_client()


async def test_lifespan_closes_client_on_error():
    with pytest.raises(ValueError):
        async with service.http_client_lifespan(None):
            client = service.get_http_client()
            raise ValueError("stopping")
    assert client.is_closed
    with pytest.raises(RuntimeError):
        service.get_http_client()
