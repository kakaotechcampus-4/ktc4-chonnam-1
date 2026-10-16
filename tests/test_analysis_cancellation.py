"""The analysis owns all work: concurrent collection, cancellation and failure cleanup."""

import asyncio
import json

import httpx
import pytest

import main
from ai.types import CaseSearchResult, ExtractedMessage
from scanner.models import CollectResult


@pytest.mark.parametrize("link", ["https://example.test/", "https://bit.ly/test"])
@pytest.mark.parametrize("finish_collection", [False, True])
async def test_cancel_reaps_message_and_only_collection(monkeypatch, link, finish_collection):
    started = {name: asyncio.Event() for name in ("message", "collector")}
    stopped = {name: asyncio.Event() for name in started}
    children = []

    async def hold(name):
        children.append(asyncio.current_task())
        started[name].set()
        try:
            if name == "collector" and finish_collection:
                return CollectResult(link, link, (), 200, "text/html", "<p>조회</p>", None, 1)
            await asyncio.Event().wait()
        finally:
            stopped[name].set()

    monkeypatch.setattr(main, "analyze_message_part", lambda text: hold("message"))
    monkeypatch.setattr(main, "collect_url_isolated", lambda url: hold("collector"))
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", {"cancel-user"})
    job_id = main.create_analysis_job("cancel-user")
    parent = asyncio.create_task(main.run_analysis_and_callback(
        [link], "취소", "https://bot-api.kakao.com/callback", "cancel-user", job_id,
    ))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 1)
        parent.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(parent, 1)
        assert len(children) == 2
        assert all(task.done() for task in children)
        assert all(event.is_set() for event in stopped.values())
        assert "cancel-user" not in main.RUNNING_USERS
    finally:
        parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)


async def test_shortener_collection_and_message_run_concurrently_once(monkeypatch):
    from ai.pipeline.results import build_message_part

    message_started = asyncio.Event()
    collector_started = asyncio.Event()
    calls = []

    async def message(text):
        message_started.set()
        await asyncio.wait_for(collector_started.wait(), 1)
        return build_message_part("", ExtractedMessage(), CaseSearchResult(), None)

    async def collector(link):
        calls.append(link)
        collector_started.set()
        await asyncio.wait_for(message_started.wait(), 1)
        return CollectResult(link, "https://www.cjlogistics.com/", ("https://attacker.test/",),
                             200, "text/html", "", None, 1)

    monkeypatch.setattr(main, "analyze_message_part", message)
    monkeypatch.setattr(main, "collect_url_isolated", collector)
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    job_id = main.create_analysis_job("u")
    await asyncio.wait_for(main.run_analysis(["https://bit.ly/test"], "", job_id), 2)
    payload = main.ANALYSIS_JOBS[job_id]["final_payload"]
    assert calls == ["https://bit.ly/test"]
    assert payload["url"]["domain"] == "attacker.test"
    assert payload["env"]["answer"] == "failed"


async def test_message_exception_still_finalizes_collection(monkeypatch):
    async def message(text):
        raise RuntimeError("synthetic message failure")

    async def collector(link):
        return CollectResult(link, None, (), None, None, "", None, 1, ("timeout",))

    monkeypatch.setattr(main, "analyze_message_part", message)
    monkeypatch.setattr(main, "collect_url_isolated", collector)
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    job_id = main.create_analysis_job("u")
    await main.run_analysis(["https://example.test/"], "m", job_id)
    payload = main.ANALYSIS_JOBS[job_id]["final_payload"]
    assert payload["message"]["answer"] == "not_run"
    assert payload["message"]["details"]["reason"]["failures"] == ["missing_result"]
    assert payload["env"]["answer"] == "failed"


async def test_callback_deadline_cancels_collection_and_preserves_timeout_result(monkeypatch):
    stopped = set()
    delivered = []

    async def hold(name):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)

    def callback(request):
        delivered.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "SUCCESS"})

    monkeypatch.setattr(main, "analyze_message_part", lambda text: hold("message"))
    monkeypatch.setattr(main, "collect_url_isolated", lambda url: hold("collector"))
    monkeypatch.setattr(main, "CALLBACK_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", {"timeout-user"})
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    job_id = main.create_analysis_job("timeout-user")
    async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        await asyncio.wait_for(main.run_analysis_and_callback(
            ["https://bit.ly/test"], "m", "https://bot-api.kakao.com/cb", "timeout-user", job_id,
        ), 2)
    job = main.ANALYSIS_JOBS[job_id]
    assert stopped == {"message", "collector"}
    assert job["status"] == "timeout"
    assert job["error"] == "analysis_timeout"
    assert job["callback_status"] == "success"
    assert delivered == [job["result"]]
    assert job["final_payload"] is None
    assert "timeout-user" not in main.RUNNING_USERS
