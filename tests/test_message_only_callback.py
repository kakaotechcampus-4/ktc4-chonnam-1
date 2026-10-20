"""Message jobs reuse callback delivery without invoking URL collection or a real LLM."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

import main
from ai.types import MessagePart


CALLBACK = "https://callback.example/result?token=synthetic"
USER = "message-user"
TEXT = "계좌 비밀번호를 입력하세요"


@pytest.fixture(autouse=True)
def state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "callback.example")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())
    monkeypatch.setattr(main, "run_analysis", AsyncMock(side_effect=AssertionError("URL path used")))
    monkeypatch.setattr(main, "collect_url_isolated", AsyncMock(side_effect=AssertionError("page collected")))


def part(answer="risk_found"):
    return MessagePart.model_validate({
        "answer": answer,
        "details": {
            "signals": [{"code": "credential_request", "evidence": TEXT}],
            "reason": {"text": "observed", "failures": ["timeout"] if answer == "partial" else []},
        },
    })


def payload(text=TEXT, callback=CALLBACK):
    request = {"utterance": text, "user": {"id": USER}}
    if callback is not None:
        request["callbackUrl"] = callback
    return {"userRequest": request}


async def skill(body, background=None):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode()}
    return await main.kakao_skill(Request({"type": "http"}, receive), background or BackgroundTasks())


@pytest.mark.parametrize("answer", ["risk_found", "partial"])
async def test_response_body_is_sent_before_llm_finishes_and_result_is_delivered(monkeypatch, answer):
    started, release, stopped, response_sent = (asyncio.Event() for _ in range(4))
    async def analyze(text):
        assert text == TEXT
        started.set()
        try:
            await release.wait()
            return part(answer)
        finally:
            stopped.set()
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    delivered, messages = [], []
    def callback(request):
        assert str(request.url) == CALLBACK
        delivered.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "SUCCESS"})
    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body":
            response_sent.set()
    async def receive():
        return {"type": "http.request", "body": json.dumps(payload()).encode(), "more_body": False}
    scope = {"type": "http", "method": "POST", "path": "/kakao/skill", "raw_path": b"/kakao/skill",
             "query_string": b"", "headers": [(b"content-type", b"application/json")],
             "scheme": "https", "http_version": "1.1", "server": ("test", 443), "client": ("test", 123)}
    async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        serving = asyncio.create_task(main.app(scope, receive, send))
        try:
            await asyncio.wait_for(response_sent.wait(), 2)
            await asyncio.wait_for(started.wait(), 2)
            body = b"".join(m["body"] for m in messages if m["type"] == "http.response.body")
            assert json.loads(body)["useCallback"] is True
            assert not stopped.is_set() and not serving.done()
            job, = main.ANALYSIS_JOBS.values()
            assert job["status"] == "running" and USER in main.RUNNING_USERS
            assert await skill(payload()) == main.render_card("k5-duplicate")
            assert await skill(payload("https://example.com/path")) == main.render_card("k5-duplicate")
            assert len(main.ANALYSIS_JOBS) == 1
            release.set()
            await asyncio.wait_for(serving, 2)
        finally:
            release.set()
            if not serving.done():
                serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
    assert stopped.is_set() and USER not in main.RUNNING_USERS
    assert job["status"] == "completed" and job["callback_status"] == "success"
    assert delivered == [job["result"]]
    assert TEXT in json.dumps(job["result"], ensure_ascii=False)
    assert job["message_payload"] == part(answer).model_dump(mode="json")
    assert job["final_payload"] is None
    assert (await main.get_analysis_job(job["job_id"]))["job"] == job
    assert job["result"]["template"]["outputs"][0] in main.handle_check_result(USER)["template"]["outputs"]
    assert "분석 결과를 찾을 수 없어요" in str(main.handle_detail_carousel(job["job_id"]))
    main.run_analysis.assert_not_awaited()
    main.collect_url_isolated.assert_not_awaited()


@pytest.mark.parametrize("failure", ["exception", "timeout", "cancel", "delivery", "cancel_delivery"])
async def test_failure_and_cancellation_preserve_job_contract(monkeypatch, failure):
    started, stopped, sending = (asyncio.Event() for _ in range(3))
    async def analyze(text):
        started.set()
        try:
            if failure in {"cancel", "timeout"}:
                await asyncio.Event().wait()
            if failure == "exception":
                raise RuntimeError("synthetic failure")
            return part()
        finally:
            stopped.set()
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    if failure == "timeout":
        monkeypatch.setattr(main, "CALLBACK_DEADLINE_SECONDS", 0.01)
    delivered = []
    async def callback(request):
        delivered.append(json.loads(request.content))
        sending.set()
        if failure == "cancel_delivery":
            await asyncio.Event().wait()
        if failure == "delivery":
            raise httpx.ConnectError("synthetic failure", request=request)
        return httpx.Response(200, json={"status": "SUCCESS"})
    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        task = asyncio.create_task(main.run_analysis_and_callback([], TEXT, CALLBACK, USER, job_id, message_only=True))
        try:
            await asyncio.wait_for(started.wait(), 2)
            if failure in {"cancel", "cancel_delivery"}:
                if failure == "cancel_delivery":
                    await asyncio.wait_for(sending.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    job = main.ANALYSIS_JOBS[job_id]
    assert stopped.is_set() and USER not in main.RUNNING_USERS
    assert job["completed_at"]
    expected = {"exception": "failed", "timeout": "timeout", "cancel": "cancelled",
                "delivery": "completed", "cancel_delivery": "completed"}
    assert job["status"] == expected[failure]
    if failure == "cancel":
        assert delivered == []
        assert job["callback_status"] == "cancelled"
        assert main.handle_check_result(USER) == main.render_card("r4-unavailable")
    else:
        assert delivered == [job["result"]]
        assert job["callback_status"] == {"delivery": "failed", "cancel_delivery": "cancelled"}.get(failure, "success")
    if failure in {"delivery", "cancel_delivery"}:
        assert job["message_payload"] == part().model_dump(mode="json")
        assert TEXT in json.dumps(job["result"], ensure_ascii=False)
    assert job["final_payload"] is None
    main.run_analysis.assert_not_awaited()


async def test_without_callback_has_bounded_wait_and_no_unreachable_job(monkeypatch):
    stopped = asyncio.Event()
    async def analyze(text):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    monkeypatch.setattr(main, "MESSAGE_SYNC_TIMEOUT_SECONDS", 0.01)
    response = await asyncio.wait_for(skill(payload(callback=None)), 2)
    assert "시간이 초과" in str(response)
    assert stopped.is_set()
    assert not main.ANALYSIS_JOBS and not main.RUNNING_USERS


async def test_invalid_callback_is_rejected_before_message_analysis(monkeypatch):
    analyze = AsyncMock()
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    background = BackgroundTasks()
    assert await skill(payload(callback="https://evil.example/"), background) == main.render_card("r4-unavailable")
    assert not background.tasks and not main.ANALYSIS_JOBS and not main.RUNNING_USERS
    analyze.assert_not_awaited()


async def test_without_callback_cancellation_cleans_user_and_blocks_duplicates(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def analyze(text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    task = asyncio.create_task(skill(payload(callback=None)))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert USER in main.RUNNING_USERS
        assert await skill(payload(callback=None)) == main.render_card("k5-duplicate")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert stopped.is_set()
    assert not main.RUNNING_USERS and not main.ANALYSIS_JOBS
