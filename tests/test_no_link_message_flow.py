"""URL 없는 문자 분석 흐름 테스트 (신규 시나리오 — "URL 검사와 문자 분석
흐름 분리").

링크가 없을 때 완전히 빈 입력만 r6-input-required로 즉답하고, 실제
문자 내용이 있으면 run_analysis()의 느린 URL 수집 경로를 타지 않고
문자 분석만으로 판단하는지 검증한다.
"""

import pytest
from fastapi.testclient import TestClient

import main
from ai.types import AnswerState, FailureCode, MessageDetails, MessagePart, Reason, SignalItem


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()
    yield
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _skill_payload(utterance: str) -> dict:
    return {
        "userRequest": {
            "utterance": utterance,
            "user": {"id": "no-link-user"},
        }
    }


class TestRunMessageOnlyAnalysis:
    async def test_returns_rendered_card_from_message_analysis(self, monkeypatch):
        async def fake_analyze(text):
            return MessagePart(
                answer=AnswerState.NO_RISK_FOUND,
                details=MessageDetails(doubts=[], signals=[], reason=Reason(text="ok")),
            )

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze)

        result = await main.run_message_only_analysis("지인 사칭 문자")

        assert result == main.render_card("r6b")

    async def test_analysis_failure_falls_back_to_unavailable_card(self, monkeypatch):
        async def fake_analyze(text):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze)

        result = await main.run_message_only_analysis("문자")

        assert result == main.render_card("r4-unavailable")


class TestKakaoSkillRoutingWithoutLinks:
    @pytest.mark.parametrize("answer", [AnswerState.RISK_FOUND, AnswerState.PARTIAL])
    def test_confirmed_message_risk_survives_without_url(self, client, monkeypatch, answer):
        async def fake_analyze(text):
            assert text == "계좌 비밀번호를 입력하세요"
            return MessagePart(
                answer=answer,
                details=MessageDetails(
                    signals=[SignalItem(code="credential_request", evidence=text)],
                    reason=Reason(text="observed", failures=[FailureCode.TIMEOUT] if answer == AnswerState.PARTIAL else []),
                ),
            )

        async def unexpected_collection(*args, **kwargs):
            pytest.fail("message-only analysis must not collect a page")

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze)
        monkeypatch.setattr(main, "collect_url_isolated", unexpected_collection)
        response = client.post("/kakao/skill", json=_skill_payload("계좌 비밀번호를 입력하세요"))
        assert response.status_code == 200
        card = response.json()["template"]["outputs"][0]["textCard"]
        assert "사칭이 의심" in card["title"]
        assert "계좌 비밀번호를 입력하세요" in card["description"]
        assert not main.ANALYSIS_JOBS

    def test_empty_utterance_gets_immediate_input_required_card(self, client):
        response = client.post("/kakao/skill", json=_skill_payload("   "))

        assert response.status_code == 200
        assert response.json() == main.render_card("r6-input-required")

    def test_non_empty_message_without_link_runs_message_only_analysis(self, client, monkeypatch):
        async def fake_analyze(text):
            assert text.strip() == "엄마 나 폰 고장나서 이 번호로 연락해. 돈 좀 보내줘"
            return MessagePart(
                answer=AnswerState.NO_RISK_FOUND,
                details=MessageDetails(doubts=[], signals=[], reason=Reason(text="ok")),
            )

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze)

        response = client.post(
            "/kakao/skill",
            json=_skill_payload("엄마 나 폰 고장나서 이 번호로 연락해. 돈 좀 보내줘"),
        )

        assert response.status_code == 200
        assert response.json() == main.render_card("r6b")

    def test_no_link_path_does_not_create_a_job(self, client, monkeypatch):
        async def fake_analyze(text):
            return MessagePart(
                answer=AnswerState.NO_RISK_FOUND,
                details=MessageDetails(doubts=[], signals=[], reason=Reason(text="ok")),
            )

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze)

        client.post("/kakao/skill", json=_skill_payload("송금 요구 문자"))

        assert main.ANALYSIS_JOBS == {}
        assert "no-link-user" not in main.RUNNING_USERS
