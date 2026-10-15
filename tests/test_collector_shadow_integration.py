import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import main
from backend.src.server.shadow_tasks import SHADOW_TASKS, shutdown_shadow_tasks

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


@pytest_asyncio.fixture
async def cancellation_scenario(monkeypatch):
    """Hold all external work at Events; retain child Tasks for assertions/cleanup."""
    scenario = SimpleNamespace(
        message_started=asyncio.Event(), collector_started=asyncio.Event(),
        scan_started=asyncio.Event(), log_started=asyncio.Event(),
        message_stopped=asyncio.Event(), collector_stopped=asyncio.Event(),
        release_message=asyncio.Event(), release_collector=asyncio.Event(),
        release_scan=asyncio.Event(), tasks={},
    )

    async def message(text):
        scenario.tasks["message"] = asyncio.current_task()
        scenario.message_started.set()
        try:
            await scenario.release_message.wait()
            return SimpleNamespace(brand=None, model_dump=lambda **kwargs: {})
        finally:
            scenario.message_stopped.set()

    async def collector(link):
        scenario.tasks["collector"] = asyncio.current_task()
        scenario.collector_started.set()
        try:
            await scenario.release_collector.wait()
            return SimpleNamespace(input_url=link, final_url=link, elapsed_ms=1,
                                   status_code=200, title="Test", redirect_chain=(), failures=())
        finally:
            scenario.collector_stopped.set()

    async def submit(link):
        scenario.scan_started.set()
        await scenario.release_scan.wait()
        return {"uuid": "cancel-test-scan"}

    original_log = main.log_collector_shadow

    async def observe_log(*args, **kwargs):
        scenario.tasks["log"] = asyncio.current_task()
        scenario.log_started.set()
        return await original_log(*args, **kwargs)

    monkeypatch.setattr(main, "ENABLE_URL_COLLECTOR_SHADOW", True)
    monkeypatch.setattr(main, "analyze_message_part", message)
    monkeypatch.setattr(main, "collect_url_isolated", collector)
    monkeypatch.setattr(main, "submit_url_scan", submit)
    monkeypatch.setattr(main, "wait_for_url_scan_result", AsyncMock(return_value={}))
    monkeypatch.setattr(main, "parse_urlscan_result", lambda result: {
        "url": "https://example.com", "final_url": "https://example.com",
        "domain": "example.com", "score": 0, "malicious": False,
        "categories": [], "brands": [],
    })
    monkeypatch.setattr(main, "log_collector_shadow", observe_log)

    scenario.tasks["parent"] = asyncio.create_task(
        main.run_analysis(["https://example.com"], "취소 테스트")
    )
    try:
        await asyncio.wait_for(scenario.message_started.wait(), timeout=1)
        await asyncio.wait_for(scenario.collector_started.wait(), timeout=1)
        await asyncio.wait_for(scenario.scan_started.wait(), timeout=1)
        yield scenario
    finally:
        # Assertions run before this safety net: cleanup must not hide the leak.
        parent = scenario.tasks["parent"]
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
        children = [task for name, task in scenario.tasks.items() if name != "parent"]
        for task in children:
            if not task.done():
                task.cancel()
        await asyncio.gather(*children, return_exceptions=True)
        await shutdown_shadow_tasks()


def child_states(scenario):
    return {
        name: {"done": scenario.tasks[name].done(),
               "cancelled": scenario.tasks[name].cancelled(),
               "finally_ran": getattr(scenario, name + "_stopped").is_set(),
               "tracked": scenario.tasks[name] in SHADOW_TASKS}
        for name in ("message", "collector")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("child", ["message", "collector"])
async def test_parent_cancel_before_shadow_handoff_cleans_child(cancellation_scenario, child):
    scenario = cancellation_scenario
    assert not scenario.log_started.is_set()
    assert scenario.tasks["collector"] in SHADOW_TASKS
    parent = scenario.tasks["parent"]
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, timeout=1)

    states = child_states(scenario)
    assert not scenario.log_started.is_set()
    assert states[child]["done"], f"Child survived pre-handoff parent cancellation: {states}"
    assert states[child]["cancelled"], states
    assert states[child]["finally_ran"], states


@pytest.mark.asyncio
async def test_parent_cancel_after_shadow_handoff_keeps_detached_collector(cancellation_scenario):
    scenario = cancellation_scenario
    scenario.release_scan.set()
    await asyncio.wait_for(scenario.log_started.wait(), timeout=1)
    collector = scenario.tasks["collector"]
    logger = scenario.tasks["log"]
    assert {collector, logger} <= SHADOW_TASKS

    parent = scenario.tasks["parent"]
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, timeout=1)

    states = child_states(scenario)
    # Parent is now awaiting message_task: cancellation propagates to that child.
    assert states["message"]["cancelled"], states
    assert states["message"]["finally_ran"], states
    # Ownership was transferred to the logger; remaining Shadow work is intentional.
    assert not collector.done()
    assert not logger.done()
    assert {collector, logger} <= SHADOW_TASKS
    scenario.release_collector.set()
    await asyncio.wait_for(logger, timeout=1)
    assert collector.done() and not collector.cancelled()
    assert scenario.collector_stopped.is_set()
    assert collector not in SHADOW_TASKS
    assert logger not in SHADOW_TASKS
