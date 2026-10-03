"""서버 기동 시 KB 워밍업과 공유 HTTP 클라이언트 수명을 함께 검증한다."""

import pytest
from fastapi.testclient import TestClient

import backend.src.server.lifespan as lifespan
import backend.src.server.urlscan_service as service
from ai.types import AnalysisStatus, CaseSearchResult


@pytest.fixture
def warm_up_calls(monkeypatch):
    calls = []

    def fake():
        calls.append("warm_up")
        return CaseSearchResult(status=AnalysisStatus.COMPLETED)

    monkeypatch.setattr(lifespan, "warm_up_case_search", fake)
    return calls


@pytest.mark.asyncio
async def test_kb_is_warmed_before_serving(warm_up_calls, capsys):
    async with lifespan.app_lifespan(None):
        assert warm_up_calls == ["warm_up"]
        client = service.get_http_client()
        assert not client.is_closed

    assert client.is_closed
    assert "[KB WARMUP]" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_warm_up_failure_does_not_block_startup(monkeypatch, capsys):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(lifespan, "warm_up_case_search", broken)

    async with lifespan.app_lifespan(None):
        client = service.get_http_client()
        assert not client.is_closed

    assert client.is_closed
    assert "[KB WARMUP FAILED] OSError: disk gone" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_empty_kb_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(
        lifespan, "warm_up_case_search",
        lambda: CaseSearchResult(status=AnalysisStatus.FALLBACK),
    )

    async with lifespan.app_lifespan(None):
        pass

    assert "[KB WARMUP EMPTY] status=fallback" in capsys.readouterr().out


def test_main_app_warms_kb_on_startup(warm_up_calls):
    import main

    with TestClient(main.app):
        assert warm_up_calls == ["warm_up"]
