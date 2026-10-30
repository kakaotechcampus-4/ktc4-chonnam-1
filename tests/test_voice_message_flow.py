"""카카오톡 음성(통화 녹음) 처리 흐름 테스트.

핵심 검증: 음성은 split_message()/기존 문자·URL 분석으로 흘러가지 않고
완전히 분리된 경로를 타는지, 다운로드 주소는 반드시 media.url(또는 M4A
파일 첨부 utterance)에서만 오는지, transcribe_call()→analyze_call() 연동이
예산·실패·취소를 안전하게 다루는지, 임시 파일이 지워지는지, 서명된 CDN
URL이 로그에 노출되지 않는지, Q1→K6/A2 대응이 쌓이는지, 기존 문자 흐름이
그대로인지.

이 모듈은 Mock 기반이다 — 실제 STT·LLM API를 호출하지 않는다. `transcribe_call`/
`analyze_call`은 main 모듈에 바인딩된 이름을 monkeypatch해서 대체한다.
"""

import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import main
from ai.case.types import (
    ClaimView,
    ClaimViewDemand,
    ClaimViewIdentity,
    Conclusion,
    DemandKind,
    IdentityKind,
    OfficialContact,
    ResponseAction,
    Scenario,
    Segment,
    Side,
    StopAction,
    TranscriptResult,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VoiceCaseResult,
)
from backend.src.server.media_guard import MediaDownloadBlocked


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()
    yield
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


SIGNED_CDN_URL = "https://media.kakao.test/rec/abc?token=should-not-leak"


def _real_temp_voice_file() -> str:
    """`run_voice_analysis()`가 실제로 열어서 bytes로 읽으므로, 가짜
    다운로드도 디스크에 진짜 존재하는 파일 경로를 돌려줘야 한다."""
    fd, path = tempfile.mkstemp(suffix="_test_voice.m4a")
    with os.fdopen(fd, "wb") as file:
        file.write(b"fake-audio-bytes")
    return path


def _voice_skill_payload(user_id: str = "voice-user", callback_url: str | None = None) -> dict:
    payload = {
        "userRequest": {
            # 실제로 확인된 동작: utterance에도 같은 서명 URL이 실린다.
            "utterance": SIGNED_CDN_URL,
            "user": {"id": user_id},
            "params": {"media": {"type": "audio", "url": SIGNED_CDN_URL}},
        },
    }
    if callback_url is not None:
        payload["userRequest"]["callbackUrl"] = callback_url
    return payload


def _transcript(text: str = "안녕하세요", failure=None, speaker_split_failed: bool = False) -> TranscriptResult:
    segments = [] if failure is not None else [Segment(side=Side.UNKNOWN, speaker="화자1", text=text)]
    return TranscriptResult(segments=segments, speaker_split_failed=speaker_split_failed, failure=failure)


def _voice_result(
    conclusion: Conclusion = Conclusion.SUSPECTED,
    caution: bool = False,
    stop_actions=None,
    official_contact: OfficialContact | None = None,
    claims: ClaimView | None = None,
    checked=None,
    unchecked=None,
    response_actions=None,
    questions=None,
    number_checks=None,
    similar_cases=None,
) -> VoiceCaseResult:
    return VoiceCaseResult(
        scenario=Scenario.VOICE_PHISHING,
        conclusion=conclusion,
        caution=caution,
        stop_actions=stop_actions or [StopAction.HANG_UP, StopAction.DONT_SEND_MONEY],
        official_contact=official_contact,
        claims=claims or ClaimView(),
        checked=checked or [],
        unchecked=unchecked or [],
        response_actions=response_actions or [],
        questions=questions or [],
        number_checks=number_checks or [],
        similar_cases=similar_cases,
    )


def _patch_pipeline(monkeypatch, *, download=None, transcript=None, analysis=None):
    """다운로드·받아쓰기·분석을 한 번에 가짜로 바꾼다. 인자를 안 주면 기본 성공값."""

    async def fake_download(url):
        if download is not None:
            return await download(url)
        return _real_temp_voice_file(), 10

    async def fake_transcribe(audio, filename):
        if transcript is None:
            return _transcript()
        outcome = transcript(audio, filename)
        return await outcome if asyncio.iscoroutine(outcome) else outcome

    async def fake_analyze(transcript_result, *, time_budget_s=40.0):
        if analysis is None:
            return _voice_result()
        outcome = analysis(transcript_result, time_budget_s=time_budget_s)
        return await outcome if asyncio.iscoroutine(outcome) else outcome

    monkeypatch.setattr(main, "download_voice_media", fake_download)
    monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
    monkeypatch.setattr(main, "analyze_call", fake_analyze)


class TestVoiceMessageIsRoutedBeforeSplitMessage:
    def test_split_message_is_never_called_for_a_voice_message(self, client, monkeypatch):
        def unexpected_split(utterance):
            pytest.fail("split_message()가 음성 메시지에도 호출됨")

        monkeypatch.setattr(main, "split_message", unexpected_split)
        _patch_pipeline(monkeypatch)

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.status_code == 200

    def test_utterance_url_is_not_used_as_download_source(self, client, monkeypatch):
        """media.url과 utterance가 다르면, 반드시 media.url 쪽이 다운로드된다."""

        captured_urls = []

        async def fake_download(url):
            captured_urls.append(url)
            return _real_temp_voice_file(), 10

        _patch_pipeline(monkeypatch, download=fake_download)

        payload = _voice_skill_payload()
        payload["userRequest"]["utterance"] = "https://attacker.example.test/not-the-real-media"
        payload["userRequest"]["params"]["media"]["url"] = SIGNED_CDN_URL

        client.post("/kakao/skill", json=payload)

        assert captured_urls == [SIGNED_CDN_URL]

    def test_m4a_file_attachment_without_params_media_is_recognized(self, client, monkeypatch):
        """파일로 첨부한 m4a는 params.media 없이 utterance에 CDN 주소만 온다
        (voice-phishing-flow.md 0절 실기기 확인)."""

        captured_urls = []

        async def fake_download(url):
            captured_urls.append(url)
            return _real_temp_voice_file(), 10

        _patch_pipeline(monkeypatch, download=fake_download)

        attachment_url = "https://media.kakao.test/f_123.m4a?credential=abc&signature=def"
        response = client.post(
            "/kakao/skill",
            json={"userRequest": {"utterance": attachment_url, "user": {"id": "voice-user"}}},
        )

        assert response.status_code == 200
        assert captured_urls == [attachment_url]

    def test_signed_cdn_url_never_appears_in_logs(self, client, monkeypatch, capsys):
        """서명된 CDN 주소(쿼리스트링의 토큰)는 성공 경로든, 다운로드·받아쓰기·
        분석 실패 경로든 어떤 print 로그에도 나오면 안 된다."""

        async def failing_transcribe(audio, filename):
            raise RuntimeError("boom")

        _patch_pipeline(monkeypatch, transcript=failing_transcribe)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        client.post("/kakao/skill", json=_voice_skill_payload())

        out = capsys.readouterr().out
        assert "token=should-not-leak" not in out
        assert SIGNED_CDN_URL not in out

    def test_plain_link_is_not_mistaken_for_a_voice_attachment(self, client, monkeypatch):
        """허용 호스트라도 오디오 확장자가 아니면 음성으로 보지 않는다."""

        def unexpected_download(url):
            pytest.fail("오디오가 아닌 링크가 음성 다운로드로 감")

        monkeypatch.setattr(main, "download_voice_media", unexpected_download)

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {
                    "utterance": "https://media.kakao.test/page.html",
                    "user": {"id": "text-user"},
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == main.render_card("r6-input-required")


class TestVoiceMessageSyncPath:
    """callbackUrl이 없는 개발/테스트 경로."""

    def test_successful_analysis_returns_voice_result_card(self, client, monkeypatch):
        result = _voice_result(
            conclusion=Conclusion.SUSPECTED,
            official_contact=OfficialContact(org="금융감독원", number="1332", source_url="https://fss.or.kr"),
            claims=ClaimView(
                identity=[ClaimViewIdentity(kind=IdentityKind.INSTITUTION, name="금융감독원", quote="금융감독원 조사국", listed_institution=True)],
                demands=[ClaimViewDemand(kind=DemandKind.MONEY_TRANSFER, quote="송금하세요")],
            ),
            checked=[],
            unchecked=[Unchecked(item=UncheckedItem.ACTUAL_SENDER, reason=UncheckedReason.CANNOT_VERIFY)],
        )

        removed = []
        _patch_pipeline(monkeypatch, analysis=lambda t, time_budget_s=40.0: result)
        monkeypatch.setattr(main.os, "remove", lambda path: removed.append(path))

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.status_code == 200
        text_card = response.json()["template"]["outputs"][0]["textCard"]
        assert text_card["title"] == "⚠️ 보이스피싱이 의심돼요"
        assert "금융감독원 대표번호(1332)로 직접 전화해 확인하세요." in text_card["description"]
        assert "통화를 끊고" in text_card["description"]
        assert len(removed) == 1

    def test_download_failure_returns_safe_card_without_crashing(self, client, monkeypatch):
        async def failing_download(url):
            raise MediaDownloadBlocked("허용되지 않은 주소로 해석됨")

        monkeypatch.setattr(main, "download_voice_media", failing_download)

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.status_code == 200
        assert response.json() == main.render_card("r4-unavailable")

    def test_running_user_duplicate_is_still_enforced(self, client, monkeypatch):
        main.RUNNING_USERS.add("voice-user")

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.json() == main.render_card("k5-duplicate")

    def test_missing_media_url_is_handled_safely(self, client):
        payload = _voice_skill_payload()
        payload["userRequest"]["params"]["media"] = {"type": "audio"}  # url 없음

        response = client.post("/kakao/skill", json=payload)

        assert response.status_code == 200
        assert response.json() == main.render_card("r4-unavailable")


class TestTranscribeAndAnalyzeWiring:
    """transcribe_call() 결과가 그대로 analyze_call()에 전달되는지, 예산
    계산이 올바른지 확인한다 — 두 함수 자체의 내부 로직은 ai 쪽 테스트의
    책임이라 여기서는 경계만 검증한다."""

    async def test_transcript_is_passed_through_to_analyze_call(self, monkeypatch):
        transcript = _transcript("테스트 발화")
        captured = {}

        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            return transcript

        async def fake_analyze(got_transcript, *, time_budget_s):
            captured["transcript"] = got_transcript
            captured["time_budget_s"] = time_budget_s
            return _voice_result()

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", fake_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        await main.run_voice_analysis(SIGNED_CDN_URL)

        assert captured["transcript"] is transcript
        # CALLBACK_DEADLINE_SECONDS(45) - 5 = 40초에서 경과 시간을 뺀 값이라
        # 40초보다 작아야 하고, 거의 즉시 끝났으니 0에 아주 가깝게 작지는 않다.
        assert 0 < captured["time_budget_s"] <= main.VOICE_ANALYZE_BUDGET_S

    async def test_insufficient_remaining_budget_skips_real_analysis_budget(self, monkeypatch):
        """남은 예산이 5초 미만이면 analyze_call()에 0초를 줘 실제 분석
        (읽기·검색 LLM 호출)을 사실상 건너뛰게 한다.

        전역 `time.monotonic`을 직접 바꿔치기하면 이벤트 루프 안쪽의 다른
        호출까지 끌어 써 테스트가 불안정해진다 — 대신 예산 상수 자체를
        아주 작게 줄이고, 받아쓰기가 그보다 조금 더 걸리게(`asyncio.sleep`)
        해서 "경과 시간이 예산을 넘겼다"를 실제로 재현한다.
        """

        monkeypatch.setattr(main, "VOICE_ANALYZE_BUDGET_S", 0.05)
        monkeypatch.setattr(main, "VOICE_MIN_ANALYZE_BUDGET_S", 0.05)

        captured = {}

        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            await asyncio.sleep(0.1)  # 예산(0.05초)보다 길게 걸리게 한다
            return _transcript()

        async def fake_analyze(transcript_result, *, time_budget_s):
            captured["time_budget_s"] = time_budget_s
            return _voice_result(conclusion=Conclusion.NOT_ANALYZABLE)

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", fake_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        await main.run_voice_analysis(SIGNED_CDN_URL)

        assert captured["time_budget_s"] == 0.0

    async def test_transcribe_failure_result_still_reaches_analyze_call(self, monkeypatch):
        """STT 실패(빈 대본)도 예외 대신 실패 표시된 TranscriptResult로
        오므로, analyze_call()에 그대로 넘겨 결과(not_analyzable)를 맡긴다."""

        from ai.types import FailureCode

        failed_transcript = _transcript(failure=FailureCode.TIMEOUT)
        captured = {}

        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            return failed_transcript

        async def fake_analyze(transcript_result, *, time_budget_s):
            captured["transcript"] = transcript_result
            return _voice_result(
                conclusion=Conclusion.NOT_ANALYZABLE,
                unchecked=[Unchecked(item=UncheckedItem.TRANSCRIPT, reason=UncheckedReason.FAILED)],
            )

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", fake_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        card = await main.run_voice_analysis(SIGNED_CDN_URL)

        assert captured["transcript"] is failed_transcript
        text_card = card["template"]["outputs"][0]["textCard"]
        assert text_card["title"] == "🎙️ 녹음 내용을 알아듣지 못했어요"

    async def test_speaker_split_failed_transcript_is_passed_through(self, monkeypatch):
        """화자 분리 실패는 대본을 살려서 넘긴다(speaker_split_failed=True) —
        분석 쪽이 그 정보로 caution·unchecked를 정한다."""

        split_failed_transcript = _transcript("텍스트는 있음", speaker_split_failed=True)
        captured = {}

        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            return split_failed_transcript

        async def fake_analyze(transcript_result, *, time_budget_s):
            captured["speaker_split_failed"] = transcript_result.speaker_split_failed
            return _voice_result(conclusion=Conclusion.INCONCLUSIVE, caution=True)

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", fake_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        await main.run_voice_analysis(SIGNED_CDN_URL)

        assert captured["speaker_split_failed"] is True

    async def test_cancelled_error_during_transcribe_is_not_swallowed(self, monkeypatch):
        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def cancelling_transcribe(audio, filename):
            raise asyncio.CancelledError()

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", cancelling_transcribe)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        with pytest.raises(asyncio.CancelledError):
            await main.run_voice_analysis(SIGNED_CDN_URL)

    async def test_cancelled_error_during_analyze_is_not_swallowed(self, monkeypatch):
        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            return _transcript()

        async def cancelling_analyze(transcript_result, *, time_budget_s):
            raise asyncio.CancelledError()

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", cancelling_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        with pytest.raises(asyncio.CancelledError):
            await main.run_voice_analysis(SIGNED_CDN_URL)

    async def test_downloaded_file_is_removed_even_when_analyze_raises(self, monkeypatch):
        removed = []

        async def fake_download(url):
            return _real_temp_voice_file(), 10

        async def fake_transcribe(audio, filename):
            return _transcript()

        async def failing_analyze(transcript_result, *, time_budget_s):
            raise RuntimeError("boom")

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "transcribe_call", fake_transcribe)
        monkeypatch.setattr(main, "analyze_call", failing_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: removed.append(path))

        card = await main.run_voice_analysis(SIGNED_CDN_URL)

        assert card == main.render_card("r4-unavailable")
        assert len(removed) == 1


class TestDownloadVoiceMediaCleansUpOnCancellation:
    """`download_voice_media()` 자체(목업으로 대체하지 않은 실제 함수)가
    다운로드 도중 취소돼도 임시 파일을 남기지 않는지 확인한다.

    `asyncio.CancelledError`는 `Exception`이 아니라 `BaseException`만
    상속해서(3.8+), `except Exception: os.remove(path)` 패턴은 취소 중
    예외를 못 잡고 파일을 남긴다 — 그 회귀를 지키는 테스트다.
    """

    async def test_temp_file_is_removed_when_cancelled_mid_download(self, monkeypatch):
        class FakeResponse:
            status_code = 200

            async def aiter_bytes(self, chunk_size):
                yield b"partial-bytes"
                raise asyncio.CancelledError()

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *exc_info):
                return False

        class FakeHttpClient:
            def stream(self, method, url, **kwargs):
                return FakeStream()

        monkeypatch.setattr(main, "get_http_client", lambda: FakeHttpClient())
        monkeypatch.setattr(main, "assert_resolves_to_public_ip", lambda host: _noop())

        created_paths = []
        import tempfile as tempfile_module
        real_mkstemp = tempfile_module.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created_paths.append(path)
            return fd, path

        monkeypatch.setattr(main.tempfile, "mkstemp", tracking_mkstemp)

        with pytest.raises(asyncio.CancelledError):
            await main.download_voice_media(SIGNED_CDN_URL)

        assert len(created_paths) == 1
        assert not os.path.exists(created_paths[0])


async def _noop():
    return None


class TestVoiceMessageAsyncCallbackPath:
    def test_callback_path_creates_job_tagged_as_voice_and_responds_analyzing(self, client, monkeypatch):
        async def fake_run_analysis_and_callback(*args, **kwargs):
            return None

        monkeypatch.setattr(main, "run_analysis_and_callback", fake_run_analysis_and_callback)

        response = client.post(
            "/kakao/skill",
            json=_voice_skill_payload(callback_url="https://bot-api.kakao.com/callback/test"),
        )

        assert response.status_code == 200
        assert response.json() == main.render_card("r5-analyzing")
        job, = main.ANALYSIS_JOBS.values()
        assert job["analysis_kind"] == "voice"

    async def test_run_analysis_and_callback_routes_to_voice_analysis(self, monkeypatch):
        """voice_media_url이 있으면 run_voice_analysis()만 호출되는지 확인한다.

        콜백 전송 자체(호스트 허용 여부 등)는 이 테스트의 관심사가 아니다 —
        콜백이 거부돼도 분석 단계(1단계)는 이미 끝난 뒤이므로 라우팅
        검증에는 영향이 없다.
        """

        calls = []

        async def fake_voice_analysis(media_url, job_id=None):
            calls.append((media_url, job_id))
            return main.render_card("r4-unavailable")

        async def unexpected(*args, **kwargs):
            pytest.fail("voice_media_url이 있는데 다른 분석 경로가 호출됨")

        monkeypatch.setattr(main, "run_voice_analysis", fake_voice_analysis)
        monkeypatch.setattr(main, "run_analysis", unexpected)

        job_id = main.create_analysis_job("voice-user")

        await main.run_analysis_and_callback(
            [], "", "https://bot-api.kakao.com/callback/test", "voice-user", job_id,
            voice_media_url=SIGNED_CDN_URL,
        )

        assert calls == [(SIGNED_CDN_URL, job_id)]

    async def test_callback_failure_marks_job_failed_without_crashing(self, monkeypatch):
        """콜백 URL이 만료·불량이어도 기존 콜백 실패 처리로 안전하게 끝난다."""

        async def fake_voice_analysis(media_url, job_id=None):
            return main.render_card("r4-unavailable")

        monkeypatch.setattr(main, "run_voice_analysis", fake_voice_analysis)

        job_id = main.create_analysis_job("voice-user")

        await main.run_analysis_and_callback(
            [], "", "https://untrusted.example.test/callback", "voice-user", job_id,
            voice_media_url=SIGNED_CDN_URL,
        )

        job = main.ANALYSIS_JOBS[job_id]
        assert job["callback_status"] == "failed"
        assert "voice-user" not in main.RUNNING_USERS

    async def test_saved_result_is_available_after_callback_failure(self, monkeypatch):
        """콜백이 실패해도 결과는 콜백 전에 이미 저장돼 있어서 '결과 확인'으로
        다시 볼 수 있다(latency-budget.md, ADR 0003 저장 우선 원칙)."""

        result = _voice_result(conclusion=Conclusion.SUSPECTED)

        _patch_pipeline(monkeypatch, analysis=lambda t, time_budget_s=40.0: result)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        job_id = main.create_analysis_job("voice-user")
        main.ANALYSIS_JOBS[job_id]["analysis_kind"] = "voice"

        await main.run_analysis_and_callback(
            [], "", "https://untrusted.example.test/callback", "voice-user", job_id,
            voice_media_url=SIGNED_CDN_URL,
        )

        job = main.ANALYSIS_JOBS[job_id]
        assert job["status"] == "completed"
        assert job["callback_status"] == "failed"
        assert job["final_payload"]["conclusion"] == "suspected"

        reply = main.handle_check_result("voice-user")
        # handle_check_result()는 확인 시각 말풍선(simpleText)을 결과 카드
        # 앞에 붙인다(K2) — 결과 카드는 그 다음 output이다.
        text_card = reply["template"]["outputs"][1]["textCard"]
        assert text_card["title"] == "⚠️ 보이스피싱이 의심돼요"


class TestVoiceAnswerFlow:
    """Q1 "이미 하신 게 있나요?" 답변 -> K6/A2."""

    def _seed_voice_job(self, response_actions=None, questions=None) -> str:
        job_id = main.create_analysis_job("voice-user")
        job = main.ANALYSIS_JOBS[job_id]
        job["analysis_kind"] = "voice"
        job["final_payload"] = _voice_result(
            conclusion=Conclusion.SUSPECTED, response_actions=response_actions or [], questions=questions or [],
        ).model_dump(mode="json")
        return job_id

    def test_sent_money_answer_returns_k6_with_112_button(self, client):
        job_id = self._seed_voice_job()

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {"utterance": "", "user": {"id": "voice-user"}},
                "action": {"clientExtra": {
                    "case_id": job_id, "question_id": "already_acted", "option_id": "sent_money",
                }},
            },
        )

        assert response.status_code == 200
        text_card = response.json()["template"]["outputs"][0]["textCard"]
        assert text_card["title"] == "🚨 지금 먼저 하세요"
        assert any(b["label"] == "112 신고" for b in text_card["buttons"])

    def test_not_yet_answer_with_no_prior_answers_returns_a2(self, client):
        job_id = self._seed_voice_job()

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {"utterance": "", "user": {"id": "voice-user"}},
                "action": {"clientExtra": {
                    "case_id": job_id, "question_id": "already_acted", "option_id": "not_yet",
                }},
            },
        )

        assert response.status_code == 200
        assert "1394 상담" in str(response.json())
        assert "🚨 지금 먼저 하세요" not in str(response.json())

    def test_installed_app_only_answer_gets_no_button(self, client):
        job_id = self._seed_voice_job()

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {"utterance": "", "user": {"id": "voice-user"}},
                "action": {"clientExtra": {
                    "case_id": job_id, "question_id": "already_acted", "option_id": "installed_app",
                }},
            },
        )

        text_card = response.json()["template"]["outputs"][0]["textCard"]
        assert text_card["buttons"] == []

    def test_answers_accumulate_across_multiple_clicks(self, client):
        """먼저 '아직 안 했어요'를 눌러도, 그 뒤 '돈을 보냈어요'를 누르면
        쌓인 답 전부로 K6가 다시 나간다(D16)."""

        job_id = self._seed_voice_job()

        main.handle_voice_answer(job_id, "not_yet")
        card = main.handle_voice_answer(job_id, "sent_money")

        text_card = card["template"]["outputs"][0]["textCard"]
        assert text_card["title"] == "🚨 지금 먼저 하세요"
        assert main.ANALYSIS_JOBS[job_id]["qa_answers"] == ["not_yet", "sent_money"]

    def test_answer_for_unknown_job_is_handled_safely(self, client):
        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {"utterance": "", "user": {"id": "voice-user"}},
                "action": {"clientExtra": {
                    "case_id": "no-such-job", "question_id": "already_acted", "option_id": "sent_money",
                }},
            },
        )

        assert response.status_code == 200
        assert "찾을 수 없어요" in str(response.json())

    async def test_q1_card_is_attached_when_questions_present(self, monkeypatch):
        """Q1은 job_id가 있는(콜백) 경로에서만 붙는다 — 답을 쌓아 둘 곳이
        있어야 하기 때문이다."""

        from ai.case.types import AttachedQuestion, QuestionId

        result = _voice_result(
            conclusion=Conclusion.SUSPECTED,
            questions=[AttachedQuestion(question_id=QuestionId.ALREADY_ACTED, option_ids=["sent_money", "not_yet"])],
        )
        _patch_pipeline(monkeypatch, analysis=lambda t, time_budget_s=40.0: result)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        job_id = main.create_analysis_job("voice-user")

        card = await main.run_voice_analysis(SIGNED_CDN_URL, job_id=job_id)

        outputs = card["template"]["outputs"]
        assert len(outputs) == 2
        list_card = outputs[1]["listCard"]
        assert list_card["header"]["title"] == "이미 하신 게 있나요?"
        assert [item["title"] for item in list_card["items"]] == ["돈이나 상품권을 보냈어요", "아직 안 했어요"]
        assert list_card["items"][0]["extra"]["case_id"] == job_id

    async def test_q1_card_is_not_attached_on_sync_path_without_job_id(self, monkeypatch):
        """동기(개발용) 경로는 job_id가 없어 답을 쌓아 둘 곳이 없다 — Q1을
        붙이면 눌러도 아무 효과가 없는 버튼이 되므로 붙이지 않는다."""

        from ai.case.types import AttachedQuestion, QuestionId

        result = _voice_result(
            conclusion=Conclusion.SUSPECTED,
            questions=[AttachedQuestion(question_id=QuestionId.ALREADY_ACTED, option_ids=["sent_money"])],
        )
        _patch_pipeline(monkeypatch, analysis=lambda t, time_budget_s=40.0: result)
        monkeypatch.setattr(main.os, "remove", lambda path: None)

        card = await main.run_voice_analysis(SIGNED_CDN_URL, job_id=None)

        assert len(card["template"]["outputs"]) == 1


class TestV1CardSurfacesNumberChecksAndSimilarCases:
    """`VoiceCaseResult.number_checks`/`similar_cases`는 chatbot-copy.md
    매핑 표에 있는 필드인데도 1차 구현에서 카드에 전혀 쓰이지 않던 것을
    2차 리뷰에서 찾아 고쳤다 — 그 회귀를 지킨다."""

    def test_official_number_check_is_shown(self):
        from ai.case.types import NumberCheck

        result = _voice_result(
            number_checks=[NumberCheck(number="1332", org="금융감독원", result="official")],
        ).model_dump(mode="json")

        card = main.render_voice_result_card(result)
        description = card["template"]["outputs"][0]["textCard"]["description"]

        assert "1332" in description
        assert "금융감독원" in description
        assert "공식 번호가 맞아요" in description

    def test_org_not_listed_is_not_phrased_as_unofficial(self):
        """org_not_listed는 "공식이 아니다"가 아니라 "대조 못함"이어야 한다."""

        from ai.case.types import NumberCheck

        result = _voice_result(
            number_checks=[NumberCheck(number="02-1234-5678", org="알 수 없는 기관", result="org_not_listed")],
        ).model_dump(mode="json")

        card = main.render_voice_result_card(result)
        description = card["template"]["outputs"][0]["textCard"]["description"]

        assert "공식 번호가 아니에요" not in description
        assert "대조하지 못했어요" in description

    def test_similar_cases_are_mentioned_without_a_raw_similarity_score(self):
        """유사도 점수를 악성 확률처럼 보여 주면 안 된다(설계 문서 경고)."""

        from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult

        similar = CaseSearchResult(
            status=AnalysisStatus.COMPLETED,
            matches=[CaseMatch(case_id="c1", similarity=0.987654, matched_variant="family_impersonation")],
        )
        result = _voice_result(similar_cases=similar).model_dump(mode="json")

        card = main.render_voice_result_card(result)
        description = card["template"]["outputs"][0]["textCard"]["description"]

        assert "비슷한" in description
        assert "0.98" not in description
        assert "98" not in description

    def test_empty_similar_cases_adds_no_line(self):
        from ai.types import AnalysisStatus, CaseSearchResult

        result = _voice_result(
            similar_cases=CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[]),
        ).model_dump(mode="json")

        card = main.render_voice_result_card(result)
        description = card["template"]["outputs"][0]["textCard"]["description"]

        assert "비슷한" not in description


class TestExistingTextAndUrlFlowsAreUnaffected:
    def test_text_message_without_media_still_works(self, client):
        """URL 없는 일반 문자는 음성 분기를 타지 않고 기존 흐름(빈 입력
        처리)을 그대로 탄다."""

        response = client.post(
            "/kakao/skill",
            json={"userRequest": {"utterance": "평범한 문자", "user": {"id": "text-user"}}},
        )

        assert response.status_code == 200
        assert response.json() == main.render_card("r6-input-required")

    def test_media_dict_without_audio_type_falls_through_to_normal_flow(self, client):
        """media가 있어도 type이 audio가 아니면 기존 흐름(빈 입력 처리)을 탄다."""

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {
                    "utterance": "   ",
                    "user": {"id": "other-media-user"},
                    "params": {"media": {"type": "image", "url": "https://media.kakao.test/x"}},
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == main.render_card("r6-input-required")


class TestVoiceDownloadCancellation:
    async def test_cancel_mid_chunk_removes_partial_temp_file(self, monkeypatch):
        """바깥 wait_for 타임아웃(CancelledError)이 청크를 받는 도중 들어와도 임시 파일을 지운다."""

        import asyncio

        import httpx

        second_chunk_started = asyncio.Event()

        async def slow_body():
            yield b"x" * 10
            second_chunk_started.set()
            await asyncio.sleep(60)
            yield b"y"

        def handler(request):
            return httpx.Response(200, content=slow_body())

        created = []
        real_mkstemp = main.tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        async def public_ip(host):
            return None

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        monkeypatch.setattr(main, "assert_resolves_to_public_ip", public_ip)
        monkeypatch.setattr(main.tempfile, "mkstemp", tracking_mkstemp)

        task = asyncio.create_task(main.download_voice_media(SIGNED_CDN_URL))
        await asyncio.wait_for(second_chunk_started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.aclose()

        assert len(created) == 1
        assert not os.path.exists(created[0])
