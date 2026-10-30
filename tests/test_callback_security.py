"""Behavioral regressions for #66; failures remain visible until the security patch lands.

Only analysis and HTTP transport are mocked. No dependency on callback_guard's
future interface, no real network, and no blanket trust in kakao.com subdomains.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import main
from backend.src.server import http_client


ALLOWED_HOST = "callback.example.test"
CALLBACK_URL = f"https://{ALLOWED_HOST}/callback"
USER = "callback-security-user"
SECRET = "test-skill-secret"
QUERY_SECRET = "private-callback-token-123"
INVALID_URLS = [
    pytest.param(f"http://{ALLOWED_HOST}/callback", id="http"),
    pytest.param(f"ftp://{ALLOWED_HOST}/callback", id="ftp"),
    pytest.param(f"https://{ALLOWED_HOST}:8443/callback", id="port-8443"),
    pytest.param("https://localhost/callback", id="localhost"),
    pytest.param("https://127.0.0.1/callback", id="loopback-v4"),
    pytest.param("https://[::1]/callback", id="loopback-v6"),
    pytest.param("https://10.0.0.1/callback", id="private-10"),
    pytest.param("https://172.16.0.1/callback", id="private-172"),
    pytest.param("https://192.168.0.1/callback", id="private-192"),
    pytest.param("https://169.254.169.254/callback", id="link-local-v4"),
    pytest.param("https://[fe80::1]/callback", id="link-local-v6"),
    pytest.param(f"https://{ALLOWED_HOST}.evil.test/callback", id="suffix-spoof"),
    pytest.param(f"https://user@{ALLOWED_HOST}/callback", id="userinfo"),
    pytest.param("https://untrusted.example.test/callback", id="external"),
    pytest.param("https://unlisted.kakao.com/callback", id="unlisted-kakao-subdomain"),
]

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()
    # Exercise real authentication rather than bypassing it with APP_ENV=test.
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", SECRET)
    monkeypatch.delenv("KAKAO_SKILL_SECRET_PREVIOUS", raising=False)
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", ALLOWED_HOST)
    yield
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()


@pytest.fixture
def analysis(monkeypatch):
    mocked = AsyncMock(return_value=main.kakao_response("분석 완료"))
    monkeypatch.setattr(main, "run_analysis", mocked)
    return mocked


@pytest.fixture
def callback_http(monkeypatch):
    @asynccontextmanager
    async def install(handler):
        requests = []

        def record(request):
            requests.append(request)
            return handler(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            monkeypatch.setattr(main, "get_http_client", lambda: client)
            yield requests

    return install


def success_response(request):
    return httpx.Response(200, json={"status": "SUCCESS"})


def start_job():
    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    return job_id


async def send_callback(url, job_id):
    await main.run_analysis_and_callback(
        utterance="테스트 문자 https://example.test/message",
        callback_url=url, user_id=USER, job_id=job_id,
    )


async def skill_request(url):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="https://skill.example.test") as client:
        return await client.post("/kakao/skill", headers={"X-Skill-Secret": SECRET}, json={
            "userRequest": {"utterance": "https://example.com/message 확인",
                            "user": {"id": USER}, "callbackUrl": url},
        })


@pytest.mark.parametrize("url", INVALID_URLS)
async def test_authenticated_request_with_invalid_callback_is_blocked_without_side_effects(
    url, monkeypatch, analysis,
):
    """Policy: authenticated request + invalid callbackUrl → HTTP 200 with the
    r4-unavailable card, while still blocking the SSRF attempt itself (no
    background task, no job, no outbound call). 200 here is a deliberate
    choice (Kakao skills conventionally expect 200), not a gap — see
    test_unauthenticated_request_is_rejected_before_any_side_effect for the
    401 case this must not be confused with."""

    callback = AsyncMock()
    monkeypatch.setattr(main, "run_analysis_and_callback", callback)
    create_job = Mock(wraps=main.create_analysis_job)
    monkeypatch.setattr(main, "create_analysis_job", create_job)
    registered = []
    original_add = main.BackgroundTasks.add_task

    def record_add(tasks, function, *args, **kwargs):
        registered.append(function)
        return original_add(tasks, function, *args, **kwargs)

    monkeypatch.setattr(main.BackgroundTasks, "add_task", record_add)
    response = await skill_request(url)

    assert registered == [], "Invalid callback registered a background task"
    create_job.assert_not_called()
    analysis.assert_not_awaited()
    callback.assert_not_awaited()
    assert main.ANALYSIS_JOBS == {}
    assert USER not in main.RUNNING_USERS
    assert response.status_code == 200
    assert response.json() == main.render_card("r4-unavailable")


async def test_unauthenticated_request_is_rejected_before_any_side_effect(monkeypatch, analysis):
    """Policy: an unauthenticated skill request must get 401/403 and never
    reach callback validation, analysis, or job bookkeeping — regardless of
    whether its callbackUrl would otherwise be valid."""

    callback = AsyncMock()
    monkeypatch.setattr(main, "run_analysis_and_callback", callback)
    create_job = Mock(wraps=main.create_analysis_job)
    monkeypatch.setattr(main, "create_analysis_job", create_job)
    registered = []
    original_add = main.BackgroundTasks.add_task

    def record_add(tasks, function, *args, **kwargs):
        registered.append(function)
        return original_add(tasks, function, *args, **kwargs)

    monkeypatch.setattr(main.BackgroundTasks, "add_task", record_add)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="https://skill.example.test",
    ) as client:
        # No X-Skill-Secret header at all — the common "forgot to configure
        # the admin console header" and "random internet request" case.
        response = await client.post("/kakao/skill", json={
            "userRequest": {
                "utterance": "https://example.com/message 확인",
                "user": {"id": USER},
                "callbackUrl": CALLBACK_URL,
            },
        })

    assert response.status_code in (401, 403)
    assert registered == [], "Unauthenticated request registered a background task"
    create_job.assert_not_called()
    analysis.assert_not_awaited()
    callback.assert_not_awaited()
    assert main.ANALYSIS_JOBS == {}
    assert USER not in main.RUNNING_USERS


async def test_wrong_secret_is_rejected_before_any_side_effect(monkeypatch, analysis):
    """Same as above but with an incorrect secret rather than a missing one."""

    callback = AsyncMock()
    monkeypatch.setattr(main, "run_analysis_and_callback", callback)
    registered = []
    original_add = main.BackgroundTasks.add_task

    def record_add(tasks, function, *args, **kwargs):
        registered.append(function)
        return original_add(tasks, function, *args, **kwargs)

    monkeypatch.setattr(main.BackgroundTasks, "add_task", record_add)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="https://skill.example.test",
    ) as client:
        response = await client.post(
            "/kakao/skill",
            headers={"X-Skill-Secret": "wrong-secret"},
            json={
                "userRequest": {
                    "utterance": "https://example.com/message 확인",
                    "user": {"id": USER},
                    "callbackUrl": CALLBACK_URL,
                },
            },
        )

    assert response.status_code in (401, 403)
    assert registered == []
    analysis.assert_not_awaited()
    callback.assert_not_awaited()
    assert main.ANALYSIS_JOBS == {}
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("url", INVALID_URLS)
async def test_direct_callback_rejects_invalid_destination(url, analysis, callback_http):
    job_id = start_job()
    async with callback_http(success_response) as requests:
        await send_callback(url, job_id)
    assert requests == [], "Direct callback invocation sent to a forbidden destination"
    assert USER not in main.RUNNING_USERS


async def test_rejected_send_preserves_result_and_cleans_user(analysis, callback_http):
    job_id = start_job()
    # Rejection must not destroy an already available result, regardless of
    # whether the future patch validates before or after analysis in this worker.
    main.ANALYSIS_JOBS[job_id].update(status="completed", result=analysis.return_value)
    async with callback_http(success_response) as requests:
        await send_callback("https://untrusted.example.test/callback", job_id)
    job = main.ANALYSIS_JOBS[job_id]
    assert job["status"] == "completed"
    assert job["result"] == analysis.return_value
    assert job["callback_status"] == "failed"
    assert USER not in main.RUNNING_USERS
    assert requests == []


async def test_302_is_not_followed_with_production_client_defaults(monkeypatch, analysis):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/metadata"})

    real_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        # Keep production client's redirect configuration; inject only transport.
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(http_client.httpx, "AsyncClient", offline_client)
    job_id = start_job()
    async with http_client.http_client_lifespan(main.app):
        await send_callback(CALLBACK_URL, job_id)
    assert len(requests) == 1
    assert str(requests[0].url) == CALLBACK_URL
    assert main.ANALYSIS_JOBS[job_id]["callback_status"] == "failed"
    assert main.ANALYSIS_JOBS[job_id]["result"] == analysis.return_value
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("url", [CALLBACK_URL, f"https://{ALLOWED_HOST}:443/callback"])
async def test_allowed_https_callback_keeps_normal_flow(url, analysis, callback_http):
    async with callback_http(success_response) as requests:
        response = await skill_request(url)
    assert response.status_code == 200
    assert response.json()["useCallback"] is True
    analysis.assert_awaited_once()
    assert len(requests) == 1
    assert json.loads(requests[0].content) == analysis.return_value
    job, = main.ANALYSIS_JOBS.values()
    assert job["status"] == "completed"
    assert job["result"] == analysis.return_value
    assert job["callback_status"] == "success"
    assert USER not in main.RUNNING_USERS


async def test_transport_failure_preserves_analysis_and_cleans_user(analysis, callback_http):
    def handler(request):
        raise httpx.ConnectError("connection unavailable", request=request)

    job_id = start_job()
    async with callback_http(handler) as requests:
        await send_callback(CALLBACK_URL, job_id)
    job = main.ANALYSIS_JOBS[job_id]
    assert len(requests) == 1
    assert job["status"] == "completed"
    assert job["result"] == analysis.return_value
    assert job["error"] is None
    assert job["callback_status"] == "failed"
    assert job["callback_error"]
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("failure", ["http-error", "transport-error", "echoed-response"])
async def test_callback_query_not_exposed_in_logs_or_errors(
    failure, analysis, callback_http, capsys, caplog,
):
    url = CALLBACK_URL + "?token=" + QUERY_SECRET

    def handler(request):
        if failure == "transport-error":
            raise httpx.ConnectError(f"Cannot connect to {request.url}", request=request)
        if failure == "http-error":
            return httpx.Response(500, text="upstream unavailable")
        return httpx.Response(200, json={"status": "SUCCESS", "debug_url": str(request.url)})

    job_id = start_job()
    async with callback_http(handler):
        await send_callback(url, job_id)
    job = main.ANALYSIS_JOBS[job_id]
    output = capsys.readouterr()
    exposed = output.out + output.err + caplog.text + str(job.get("callback_error")) + str(job.get("error"))
    assert job["result"] == analysis.return_value
    assert USER not in main.RUNNING_USERS
    assert QUERY_SECRET not in exposed


async def test_rejected_url_query_not_exposed(analysis, callback_http, capsys, caplog):
    url = "https://untrusted.example.test/callback?token=" + QUERY_SECRET
    job_id = start_job()
    async with callback_http(success_response) as requests:
        await send_callback(url, job_id)
    output = capsys.readouterr()
    exposed = output.out + output.err + caplog.text + str(main.ANALYSIS_JOBS[job_id].get("callback_error"))
    assert QUERY_SECRET not in exposed
    assert USER not in main.RUNNING_USERS
    assert requests == []
