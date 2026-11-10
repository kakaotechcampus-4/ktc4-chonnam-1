"""#163: 실제 결과 버튼의 extra가 저장된 Job의 상세 조회로 이어져야 한다."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import main
from ai.case.types import (
    AttachedQuestion, Conclusion, QuestionId, Scenario, VoiceCaseResult,
)


USER = "detail-test-user"
SECRET = "synthetic-skill-secret"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", SECRET)
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())


def voice_payload(conclusion=Conclusion.INCONCLUSIVE, caution=True, with_question=False):
    return VoiceCaseResult(
        scenario=Scenario.VOICE_PHISHING, conclusion=conclusion, caution=caution,
        questions=[AttachedQuestion(question_id=QuestionId.ALREADY_ACTED,
                                    option_ids=["sent_money", "not_yet"])] if with_question else [],
    ).model_dump(mode="json")


def buttons(card):
    output = card["template"]["outputs"][0]
    return (output.get("textCard") or output["basicCard"])["buttons"]


@pytest.mark.parametrize("conclusion,caution", [
    (Conclusion.SUSPECTED, False), (Conclusion.INCONCLUSIVE, True), (Conclusion.INCONCLUSIVE, False),
])
@pytest.mark.parametrize("with_question", [False, True])
def test_voice_detail_button_has_job_id_without_changing_other_actions(conclusion, caution, with_question):
    job_id = main.create_analysis_job(USER)
    payload = voice_payload(conclusion, caution, with_question)
    card = main.render_voice_result_card(payload, job_id)
    detail, restart = buttons(card)
    assert detail == {"label": "자세히 보기", "action": "block",
                      "blockId": "6aad3abfe745d3358bd90c8d", "extra": {"job_id": job_id}}
    assert restart == {"label": "다른 녹음 확인하기", "action": "block", "blockId": "6ae3302425d4bc48e2bb8a61"}
    assert len(card["template"]["outputs"]) == (2 if with_question else 1)
    if with_question:
        items = card["template"]["outputs"][1]["listCard"]["items"]
        assert [item["extra"] for item in items] == [
            {"case_id": job_id, "question_id": "already_acted", "option_id": option}
            for option in ("sent_money", "not_yet")
        ]


@pytest.mark.parametrize("voice", [True, False], ids=["voice", "existing-text"])
async def test_real_button_click_uses_existing_detail_without_starting_analysis(monkeypatch, voice):
    job_id = main.create_analysis_job(USER)
    if voice:
        payload = voice_payload(with_question=True)
        card = main.render_voice_result_card(payload, job_id)
        expected = main.render_voice_detail_carousel(payload)
    else:
        payload = {
            "url": {"domain": "example.test", "final_url": "https://example.test", "official": "not_registered"},
            "message": {"brand": None, "category": None, "answer": "no_risk_found",
                        "details": {"signals": [], "doubts": [], "reason": {"text": "확인 결과", "failures": []}}},
            "env": {"answer": "not_run", "details": {"reason": {"failures": []}}}, "result": False,
        }
        card = main.render_result_card(payload, job_id)
        expected = main.render_detail_carousel(payload)
    main.ANALYSIS_JOBS[job_id].update(
        status="completed", final_payload=payload, result=card,
        analysis_kind="voice" if voice else "url",
    )
    extra = next(button for button in buttons(card) if button["label"] == "자세히 보기")["extra"]
    spies = {}
    for name in ("read_message", "run_analysis", "run_analysis_and_callback", "run_voice_analysis"):
        spies[name] = AsyncMock(side_effect=AssertionError("detail click must not start analysis"))
        monkeypatch.setattr(main, name, spies[name])
    create_job = Mock(side_effect=AssertionError("detail click must not create a Job"))
    monkeypatch.setattr(main, "create_analysis_job", create_job)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test") as client:
        response = await client.post("/kakao/skill", headers={"X-Skill-Secret": SECRET}, json={
            "userRequest": {"utterance": "자세히 보기", "user": {"id": USER}},
            "action": {"clientExtra": extra},
        })
    assert response.status_code == 200
    assert response.json() == expected
    assert "carousel" in expected["template"]["outputs"][0]
    assert list(main.ANALYSIS_JOBS) == [job_id]
    assert main.ANALYSIS_JOBS[job_id]["final_payload"] == payload
    create_job.assert_not_called()
    for spy in spies.values():
        spy.assert_not_called()
    assert not main.RUNNING_USERS


@pytest.mark.parametrize("state", ["unknown", "running", "empty-payload"])
async def test_invalid_detail_reference_keeps_safe_fallback(monkeypatch, state):
    job_id = "missing-job"
    if state != "unknown":
        job_id = main.create_analysis_job(USER)
        main.ANALYSIS_JOBS[job_id].update(
            analysis_kind="voice", status="running" if state == "running" else "completed",
            final_payload=None if state == "running" else {},
        )
    before = set(main.ANALYSIS_JOBS)
    create_job = Mock(side_effect=AssertionError("invalid reference must not create a Job"))
    read = AsyncMock(side_effect=AssertionError("invalid reference must not analyze text"))
    monkeypatch.setattr(main, "create_analysis_job", create_job)
    monkeypatch.setattr(main, "read_message", read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test") as client:
        response = await client.post("/kakao/skill", headers={"X-Skill-Secret": SECRET}, json={
            "userRequest": {"utterance": "자세히 보기", "user": {"id": USER}},
            "action": {"clientExtra": {"job_id": job_id}},
        })
    assert response.status_code == 200
    assert response.json() == main.kakao_response(main._CASE_NOT_FOUND_TEXT)
    assert set(main.ANALYSIS_JOBS) == before
    create_job.assert_not_called()
    read.assert_not_called()


def test_rendering_without_job_does_not_invent_detail_reference():
    card = main.render_voice_result_card(voice_payload(), job_id=None)
    assert all("extra" not in button for button in buttons(card))


@pytest.mark.parametrize("conclusion,card_name", [
    (Conclusion.NOT_ANALYZABLE, "ve3-unrecognized"), (Conclusion.INPUT_REQUIRED, "ve4-no-claim"),
])
def test_voice_failure_cards_remain_unchanged(conclusion, card_name):
    assert main.render_voice_result_card(voice_payload(conclusion, False), "job") == main.render_card(card_name)
