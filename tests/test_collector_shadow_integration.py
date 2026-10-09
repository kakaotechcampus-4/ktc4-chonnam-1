import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from backend.src.server.shadow_tasks import shutdown_shadow_tasks

from scanner.isolation_client import IsolationClientError


@pytest.mark.asyncio
async def test_run_analysis_survives_collector_failure(monkeypatch, capsys):
    """격리환경 호출이 실패해도 기존 분석 결과는 정상 반환한다."""

    link = "https://example.com"
    collector_called = asyncio.Event()

    async def failing_collector(url):
        collector_called.set()
        raise IsolationClientError("격리환경 API 연결 실패")

    async def fake_submit_url_scan(url):
        return {"uuid": "test-scan-id"}

    async def fake_wait_for_url_scan_result(scan_id):
        return {"dummy": True}

    class FakeMessage:
        brand = None

        def model_dump(self, mode="json"):
            return {"brand": None}

    class FakeFinalResult:
        def model_dump(self, mode="json"):
            return {"result": "normal-analysis"}

    finalize_mock = AsyncMock(return_value=FakeFinalResult())

    monkeypatch.setattr(
        main, "ENABLE_URL_COLLECTOR_SHADOW", True
    )
    monkeypatch.setattr(
        main, "collect_url_isolated", failing_collector
    )
    monkeypatch.setattr(
        main, "submit_url_scan", fake_submit_url_scan
    )
    monkeypatch.setattr(
        main, "wait_for_url_scan_result",
        fake_wait_for_url_scan_result,
    )
    monkeypatch.setattr(
        main,
        "parse_urlscan_result",
        lambda result: {
            "url": link,
            "final_url": link,
            "domain": "example.com",
            "score": 0,
            "malicious": False,
            "categories": [],
            "brands": [],
        },
    )
    monkeypatch.setattr(
        main,
        "analyze_message_part",
        AsyncMock(return_value=FakeMessage()),
    )
    monkeypatch.setattr(
        main, "finalize_analysis", finalize_mock
    )
    monkeypatch.setattr(
        main,
        "build_ai_url_analysis",
        lambda parsed_result, brand: SimpleNamespace(
            domain="example.com",
            official=False,
        ),
    )
    monkeypatch.setattr(
        main,
        "render_result_card",
        lambda payload, **kwargs: {
            "test": "card",
            "payload": payload,
        },
    )
    monkeypatch.setattr(
        main,
        "merge_kakao_responses",
        lambda responses: {"responses": responses},
    )

    try:
        result = await asyncio.wait_for(
            main.run_analysis(
                links=[link],
                message="테스트 메시지",
            ),
            timeout=2,
        )

        # 기존 분석 결과가 정상적으로 반환되어야 한다.
        assert result == {
            "responses": [
                {
                    "test": "card",
                    "payload": {
                        "result": "normal-analysis",
                    },
                }
            ]
        }

        # 격리환경 수집이 실제로 시도되었는지 확인한다.
        assert collector_called.is_set()

        # 최종 AI 분석은 정상적으로 호출되어야 한다.
        finalize_mock.assert_awaited_once()
        assert finalize_mock.await_args.kwargs["page"] is None

    finally:
        # Shadow 로그 태스크가 오류를 처리할 때까지 기다린다.
        await shutdown_shadow_tasks()

    output = capsys.readouterr().out

    # 수집 실패가 분석 실패로 전파되지 않아야 한다.
    assert "[COLLECTOR SHADOW ERROR]" in output
    assert "[ANALYSIS ERROR]" not in output



@pytest.mark.asyncio
async def test_run_analysis_does_not_wait_for_collector(monkeypatch):
    """Collector가 끝나지 않아도 run_analysis가 반환되는지 검증한다."""

    collector_started = asyncio.Event()
    release_collector = asyncio.Event()

    async def slow_collector(link):
        collector_started.set()
        await release_collector.wait()
        return SimpleNamespace(
            input_url=link,
            final_url=link,
            elapsed_ms=10000,
            status_code=200,
            title="Test",
            redirect_chain=[],
            failures=[],
        )

    async def fake_submit_url_scan(link):
        return {"uuid": "test-scan-id"}

    async def fake_wait_for_url_scan_result(scan_id):
        return {"dummy": True}

    class FakeModel:
        brand = None

        def model_dump(self, mode="json"):
            return {}

    monkeypatch.setattr(
        main, "ENABLE_URL_COLLECTOR_SHADOW", True
    )
    monkeypatch.setattr(
        main, "collect_url_isolated", slow_collector
    )
    monkeypatch.setattr(
        main, "submit_url_scan", fake_submit_url_scan
    )
    monkeypatch.setattr(
        main, "wait_for_url_scan_result",
        fake_wait_for_url_scan_result
    )
    monkeypatch.setattr(
        main,
        "parse_urlscan_result",
        lambda result: {
            "url": "https://example.com",
            "final_url": "https://example.com",
            "domain": "example.com",
            "score": 0,
            "malicious": False,
            "categories": [],
            "brands": [],
        },
    )
    monkeypatch.setattr(
        main,
        "analyze_message_part",
        AsyncMock(return_value=FakeModel()),
    )
    monkeypatch.setattr(
        main,
        "finalize_analysis",
        AsyncMock(return_value=FakeModel()),
    )
    monkeypatch.setattr(
        main,
        "build_ai_url_analysis",
        lambda parsed_result, brand: SimpleNamespace(
            domain="example.com",
            official=False,
        ),
    )
    monkeypatch.setattr(
        main,
        "render_result_card",
        lambda *args, **kwargs: {"test": "card"},
    )
    monkeypatch.setattr(
        main,
        "merge_kakao_responses",
        lambda responses: {"responses": responses},
    )

    try:
        result = await asyncio.wait_for(
            main.run_analysis(
                links=["https://example.com"],
                message="테스트 메시지",
            ),
            timeout=1,
        )

        assert result is not None
        assert collector_started.is_set()

        # run_analysis가 반환됐지만 Collector는 아직 완료되지 않았다.
        assert not release_collector.is_set()

    finally:
        release_collector.set()
        await shutdown_shadow_tasks()