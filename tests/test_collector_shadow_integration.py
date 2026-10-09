import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from backend.src.server.shadow_tasks import shutdown_shadow_tasks


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
        main, "collect_url", slow_collector
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