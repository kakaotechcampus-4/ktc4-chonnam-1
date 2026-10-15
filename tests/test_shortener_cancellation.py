"""shortener_task가 부모 run_analysis() 취소 시 회수되는지 검증한다.

단축 주소(bit.ly 등)를 받으면 main.run_analysis()는 격리 서버로 실제
호스트를 찾는 shortener_task를 urlscan 흐름보다 먼저 만들어 두고,
결과가 필요한 시점(build_ai_url_analysis 직전)에야 await한다. 그 사이
부모가 취소되면(예: run_analysis_and_callback의 45초 타임아웃) 이
task가 끝까지 await되지 않은 채 이벤트 루프에 남을 수 있다 — Collector
Shadow task에서 이미 고친 것과 같은 종류의 누수다
(tests/test_collector_shadow_integration.py 참고).
"""

import asyncio
from types import SimpleNamespace

import pytest

import main


@pytest.mark.asyncio
async def test_shortener_task_is_cleaned_up_when_parent_is_cancelled_mid_urlscan(monkeypatch):
    resolve_started = asyncio.Event()
    resolve_stopped = asyncio.Event()
    scan_started = asyncio.Event()
    release_scan = asyncio.Event()
    shortener_task_holder: dict[str, asyncio.Task] = {}

    async def fake_resolve_real_host(link):
        shortener_task_holder["task"] = asyncio.current_task()
        resolve_started.set()
        try:
            await asyncio.sleep(999)
        finally:
            resolve_stopped.set()

    async def fake_submit(link):
        scan_started.set()
        await release_scan.wait()
        return {"uuid": "shortener-cancel-test"}

    async def fake_message(text):
        return SimpleNamespace(brand=None, model_dump=lambda **kwargs: {})

    monkeypatch.setattr(main, "resolve_real_host", fake_resolve_real_host)
    monkeypatch.setattr(main, "submit_url_scan", fake_submit)
    monkeypatch.setattr(main, "analyze_message_part", fake_message)

    parent = asyncio.create_task(
        main.run_analysis(["https://bit.ly/abc"], "단축 주소 취소 테스트")
    )

    try:
        # shortener_task가 실제로 스케줄돼 돌기 시작한 뒤, urlscan 제출
        # 단계에서 아직 await되지 않은 채 떠 있는 상태를 만든다.
        await asyncio.wait_for(resolve_started.wait(), timeout=1)
        await asyncio.wait_for(scan_started.wait(), timeout=1)

        parent.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(parent, timeout=1)

        shortener_task = shortener_task_holder["task"]
        assert shortener_task.done(), (
            "shortener_task가 부모 취소 후에도 끝나지 않았다 (누수)"
        )
        assert shortener_task.cancelled()
        assert resolve_stopped.is_set(), (
            "shortener_task의 finally가 실행되지 않았다"
        )
    finally:
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
        task = shortener_task_holder.get("task")
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
