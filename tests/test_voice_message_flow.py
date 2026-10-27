"""카카오톡 내부 음성 메시지 처리 흐름 테스트.

핵심 검증: 음성 메시지는 split_message()/기존 문자·URL 분석으로
흘러가지 않고 완전히 분리된 경로를 타는지, 다운로드 주소는 반드시
media.url에서만 오는지(utterance의 서명된 URL을 출처로 쓰지 않는지),
다운로드 실패·콜백 실패가 안전하게 끝나는지, 임시 파일이 정리되는지.
"""

import os

import pytest
from fastapi.testclient import TestClient

import main
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


class TestVoiceMessageIsRoutedBeforeSplitMessage:
    def test_split_message_is_never_called_for_a_voice_message(self, client, monkeypatch):
        def unexpected_split(utterance):
            pytest.fail("split_message()가 음성 메시지에도 호출됨")

        async def fake_download(url):
            assert url == SIGNED_CDN_URL
            return "/tmp/fake-voice.m4a", 10

        async def fake_mock_analyze(path, filename):
            return {"scenario": "voice_phishing", "conclusion": "주의 필요", "evidence": [], "guidance": "g"}

        monkeypatch.setattr(main, "split_message", unexpected_split)
        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "mock_analyze_voice_case", fake_mock_analyze)

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.status_code == 200

    def test_utterance_url_is_not_used_as_download_source(self, client, monkeypatch):
        """media.url과 utterance가 다르면, 반드시 media.url 쪽이 다운로드된다."""

        captured_urls = []

        async def fake_download(url):
            captured_urls.append(url)
            return "/tmp/fake-voice.m4a", 10

        async def fake_mock_analyze(path, filename):
            return {"scenario": "voice_phishing", "conclusion": "c", "evidence": [], "guidance": "g"}

        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "mock_analyze_voice_case", fake_mock_analyze)

        payload = _voice_skill_payload()
        payload["userRequest"]["utterance"] = "https://attacker.example.test/not-the-real-media"
        payload["userRequest"]["params"]["media"]["url"] = SIGNED_CDN_URL

        client.post("/kakao/skill", json=payload)

        assert captured_urls == [SIGNED_CDN_URL]


class TestVoiceMessageSyncPath:
    """callbackUrl이 없는 개발/테스트 경로."""

    def test_successful_analysis_returns_voice_result_card(self, client, monkeypatch):
        async def fake_download(url):
            return "/tmp/fake-voice.m4a", 123

        async def fake_mock_analyze(path, filename):
            return {
                "scenario": "voice_phishing", "conclusion": "주의 필요",
                "evidence": [{"text": "증거", "source": "mock"}], "guidance": "안내",
            }

        removed = []
        monkeypatch.setattr(main, "download_voice_media", fake_download)
        monkeypatch.setattr(main, "mock_analyze_voice_case", fake_mock_analyze)
        monkeypatch.setattr(main.os, "remove", lambda path: removed.append(path))

        response = client.post("/kakao/skill", json=_voice_skill_payload())

        assert response.status_code == 200
        text_card = response.json()["template"]["outputs"][0]["textCard"]
        assert "주의 필요" in text_card["title"]
        assert "증거" in text_card["description"]
        assert removed == ["/tmp/fake-voice.m4a"]

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


class TestExistingTextAndUrlFlowsAreUnaffected:
    def test_text_message_without_media_still_works(self, client):
        """URL 없는 일반 문자는 음성 분기를 타지 않고 기존 흐름(빈 입력
        처리)을 그대로 탄다 — 이 격리 브랜치에는 문자 전용 분석 시나리오가
        포함되지 않으므로, origin/test/render의 기존 동작(r6-input-required)을
        기준으로 검증한다."""

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
