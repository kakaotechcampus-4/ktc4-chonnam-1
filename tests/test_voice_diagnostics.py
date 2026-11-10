"""#108 BE 단계 진단: 고정 상태·건수·시간만 남기고 결과 정책은 유지한다."""

import asyncio
import io
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
import wave

import httpx
import pytest

import backend.src.server.voice_flow as voice_flow
import main
from services.kakao_card_renderer import render_card
from ai.case.types import Conclusion, Scenario, Segment, Side, TranscriptResult, VoiceCaseResult
from ai.types import FailureCode


USER = "private-user-identity"
TEXT = "private-message-content 010-1234-5678"
TRANSCRIPT = "private-transcript-content"
MEDIA = "https://media.example.test/file.m4a?signature=private-media-signature"
CALLBACK = "https://callback.example.test/path?token=private-callback-token"
SECRET = "private-auth-secret"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", SECRET)
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "callback.example.test")
    monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.example.test")


@pytest.fixture
def pipeline(monkeypatch):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    data = stream.getvalue()

    async def download(url):
        return data

    transcript = TranscriptResult(segments=[Segment(side=Side.UNKNOWN, text=TRANSCRIPT)])
    result = VoiceCaseResult(scenario=Scenario.VOICE_PHISHING, conclusion=Conclusion.INCONCLUSIVE)
    download_mock = AsyncMock(side_effect=download)
    stt = AsyncMock(return_value=transcript)
    analyze = AsyncMock(return_value=result)
    monkeypatch.setattr(voice_flow, "download_voice_media", download_mock)
    monkeypatch.setattr(voice_flow, "transcribe_call", stt)
    monkeypatch.setattr(voice_flow, "analyze_call", analyze)
    return SimpleNamespace(download=download_mock, stt=stt, analyze=analyze)


def assert_private(output):
    for secret in (USER, TEXT, TRANSCRIPT, MEDIA, CALLBACK, SECRET,
                   "private-media-signature", "private-callback-token"):
        assert secret not in output


async def deliver(monkeypatch, job_id, *, voice=True, callback_body=None, callback_error=None):
    delivered = []

    def handler(request):
        if callback_error:
            raise callback_error
        delivered.append(json.loads(request.content))
        return httpx.Response(200, json=callback_body or {"status": "SUCCESS"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        monkeypatch.setattr(voice_flow, "get_http_client", lambda: client)
        await main.run_analysis_and_callback(
            TEXT, CALLBACK, USER, job_id, voice_media_url=MEDIA if voice else None,
        )
    return delivered


async def test_voice_stage_times_and_remaining_budget(monkeypatch, pipeline, capsys):
    clock = [0.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(voice_flow, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    download, stt, analyze = pipeline.download.side_effect, pipeline.stt.return_value, pipeline.analyze.return_value

    async def timed_download(url):
        clock[0] += 3
        return await download(url)

    async def timed_stt(*args):
        clock[0] += 4
        return stt

    async def timed_analyze(*args, **kwargs):
        clock[0] += 2
        return analyze

    pipeline.download.side_effect = timed_download
    pipeline.stt.side_effect = timed_stt
    pipeline.analyze.side_effect = timed_analyze
    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    delivered = await deliver(monkeypatch, job_id)
    output = capsys.readouterr().out

    assert_private(output)
    prefix = f"[VOICE STAGE] job_id={job_id}"
    assert f"{prefix} stage=download status=completed elapsed_s=3.000 bytes=" in output
    assert f"{prefix} stage=stt status=completed elapsed_s=4.000 failure=none segments=1 chars={len(TRANSCRIPT)}" in output
    assert f"{prefix} stage=analysis status=completed elapsed_s=2.000" in output
    assert f"{prefix} stage=card status=completed elapsed_s=9.000 card=v1-voice-result" in output
    assert f"[CALLBACK RESULT STATUS] job_id={job_id} status=SUCCESS" in output
    pipeline.analyze.assert_awaited_once_with(stt, time_budget_s=33.0)
    assert delivered == [main.ANALYSIS_JOBS[job_id]["result"]]
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("case,stage,status,card", [
    ("download-error", "download", "failed", "r4-unavailable"),
    ("download-timeout", "download", "failed", "r4-unavailable"),
    ("stt-error", "stt", "failed", None),
    ("stt-failure-result", "stt", "failed", "ve3-unrecognized"),
    ("stt-empty", "stt", "completed", "ve3-unrecognized"),
    ("no-claim", "analysis", "completed", "ve4-no-claim"),
    ("analysis-error", "analysis", "failed", None),
])
async def test_voice_failures_keep_existing_card_and_safe_logs(monkeypatch, pipeline, capsys, case, stage, status, card):
    sensitive_error = RuntimeError(f"{MEDIA} {TEXT} {USER} {TRANSCRIPT}")
    if case.startswith("download-"):
        pipeline.download.side_effect = httpx.ReadTimeout(MEDIA) if case.endswith("timeout") else sensitive_error
    elif case == "stt-error":
        pipeline.stt.side_effect = sensitive_error
    elif case in ("stt-failure-result", "stt-empty"):
        pipeline.stt.return_value = TranscriptResult(
            segments=[], failure=FailureCode.TIMEOUT if case == "stt-failure-result" else None,
        )
        pipeline.analyze.return_value = VoiceCaseResult(
            scenario=Scenario.VOICE_PHISHING, conclusion=Conclusion.NOT_ANALYZABLE,
            failures=[FailureCode.TIMEOUT] if case == "stt-failure-result" else [],
        )
    elif case == "no-claim":
        pipeline.analyze.return_value = VoiceCaseResult(
            scenario=Scenario.VOICE_PHISHING, conclusion=Conclusion.INPUT_REQUIRED,
        )
    else:
        pipeline.analyze.side_effect = sensitive_error

    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    delivered = await deliver(monkeypatch, job_id)
    output = capsys.readouterr().out
    assert_private(output)
    assert f"job_id={job_id} stage={stage} status={status} elapsed_s=" in output
    assert f"job_id={job_id} stage=card status=completed" in output
    job = main.ANALYSIS_JOBS[job_id]
    assert job["callback_status"] == "success"
    assert delivered == [job["result"]]
    if card:
        assert delivered[0] == render_card(card)
        assert f"card={card}" in output
        assert job["status"] == "completed"  # VE3/R4 카드 반환은 Job 예외와 다르다
    else:
        assert job["status"] == "failed"
        assert card is None and "analysis-error-message" in output
        assert_private(str(job["error"]))
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("kind", ["text", "voice", "attachment"])
async def test_actual_skill_request_does_not_log_private_data(monkeypatch, pipeline, capsys, kind):
    text_analysis = AsyncMock(return_value=main.kakao_response("분석 완료"))
    monkeypatch.setattr(main, "run_analysis", text_analysis)

    def handler(request):
        return httpx.Response(200, json={"status": "SUCCESS"})

    request = {"utterance": MEDIA if kind == "attachment" else TEXT,
               "user": {"id": USER}, "callbackUrl": CALLBACK}
    if kind == "voice":
        request["params"] = {"media": {"type": "audio", "url": MEDIA}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as callback:
        monkeypatch.setattr(main, "get_http_client", lambda: callback)
        monkeypatch.setattr(voice_flow, "get_http_client", lambda: callback)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test") as client:
            response = await client.post("/kakao/skill", headers={"X-Skill-Secret": SECRET}, json={"userRequest": request})
    assert response.json() == render_card("r5-analyzing")
    output = capsys.readouterr().out
    assert_private(output)
    job_id, job = next(iter(main.ANALYSIS_JOBS.items()))
    assert f"[JOB CREATED] job_id={job_id}" in output
    assert f"[RUNNING USER REMOVED] job_id={job_id}" in output
    assert job["callback_status"] == "success"
    if kind == "text":
        text_analysis.assert_awaited_once()
    else:
        pipeline.stt.assert_awaited_once()


async def test_voice_deadline_logs_cancel_and_r4_without_user(monkeypatch, pipeline, capsys):
    assert main.CALLBACK_DEADLINE_SECONDS == 45.0
    assert main.READ_MESSAGE_TIMEOUT_SECONDS == 15.0
    stopped = asyncio.Event()

    async def blocked(url):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    pipeline.download.side_effect = blocked
    monkeypatch.setattr(main, "CALLBACK_DEADLINE_SECONDS", 0.01)
    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    await deliver(monkeypatch, job_id)
    output = capsys.readouterr().out
    assert_private(output)
    assert stopped.is_set()
    assert f"[ANALYSIS TIMEOUT] job_id={job_id}" in output
    assert f"job_id={job_id} stage=download status=cancelled" in output
    assert "card=r4-unavailable" in output
    assert main.ANALYSIS_JOBS[job_id]["status"] == "timeout"
    assert main.ANALYSIS_JOBS[job_id]["result"] == render_card("r4-unavailable")
    assert USER not in main.RUNNING_USERS


@pytest.mark.parametrize("failure", ["reject", "network", "reflected-status"])
async def test_callback_failure_is_correlated_without_echoing_data(monkeypatch, pipeline, capsys, failure):
    job_id = main.create_analysis_job(USER)
    main.RUNNING_USERS.add(USER)
    kwargs = {}
    if failure == "network":
        kwargs["callback_error"] = httpx.ConnectError(CALLBACK)
    else:
        kwargs["callback_body"] = {"status": "FAIL" if failure == "reject" else TEXT}
    await deliver(monkeypatch, job_id, **kwargs)
    output = capsys.readouterr().out
    assert_private(output)
    assert f"[JOB CALLBACK FAILED] job_id={job_id}" in output
    job = main.ANALYSIS_JOBS[job_id]
    assert job["status"] == "completed" and job["result"]
    assert job["callback_status"] == "failed"
    assert_private(str(job["callback_error"]))
    assert USER not in main.RUNNING_USERS
    assert all(float(value) >= 0 for value in re.findall(r"elapsed_s=([0-9.]+)", output))
