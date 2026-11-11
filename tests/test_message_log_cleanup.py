"""#139 공통 안내와 카카오 요청 로그의 개인정보 비노출 회귀 테스트."""

import httpx
import pytest

import main
from ai.types import FailureCode


NOTICE = "분석 결과를 찾을 수 없어요. 문자나 통화 녹음을 다시 보내 주세요."


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", "synthetic-secret")
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "callback.example.test")


@pytest.mark.parametrize("kind", ["missing-detail", "empty-detail", "missing-voice", "invalid-voice"])
def test_missing_result_notice_accepts_text_or_recording(kind):
    if kind == "missing-detail":
        response = main.handle_detail_carousel("missing")
    elif kind == "empty-detail":
        main.ANALYSIS_JOBS["empty"] = {"final_payload": None}
        response = main.handle_detail_carousel("empty")
    elif kind == "missing-voice":
        response = main.handle_voice_answer("missing", "not_yet", None)
    else:
        main.ANALYSIS_JOBS["invalid"] = {"analysis_kind": "voice", "final_payload": {"invalid": True}}
        response = main.handle_voice_answer("invalid", "not_yet", None)
    assert response == main.kakao_response(NOTICE)


def test_missing_message_answer_uses_common_notice_and_keeps_option_value():
    response = main.handle_message_answer("missing", "already_acted", "not_yet", None)
    assert response["template"]["outputs"][0] == {"simpleText": {"text": NOTICE}}
    assert "not_yet" in main._MESSAGE_QUESTION_OPTIONS["already_acted"]


async def test_too_long_text_does_not_require_a_url(stub_read_message):
    stub_read_message(failure=FailureCode.INPUT_TOO_LARGE)
    response = await main.run_analysis("가" * 9000)
    assert response == main.kakao_response(
        "문자가 너무 길어서 확인하지 못했어요.\n\n확인하고 싶은 부분만 복사해 다시 보내 주세요."
    )


@pytest.mark.parametrize("voice", [False, True], ids=["text", "voice"])
async def test_request_log_omits_content_identity_and_url_credentials(capsys, voice):
    utterance = "민감한 가족 문자 010-1234-5678 https://name:private-password@example.test/?token=private-token"
    request = {
        "utterance": utterance, "user": {"id": "private-user-id"},
        "callbackUrl": "https://callback.example.test/private-callback?token=private-callback-token",
    }
    if voice:
        request["params"] = {"media": {
            "type": "audio", "url": "https://media.example.test/file.wav?signature=private-signature",
        }}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test",
    ) as client:
        response = await client.post("/kakao/skill", headers={"X-Skill-Secret": "synthetic-secret"}, json={
            "userRequest": request, "action": {"clientExtra": {"job_id": "missing"}},
        })
    assert response.status_code == 200
    assert response.json() == main.kakao_response(NOTICE)
    output = capsys.readouterr().out
    for secret in (utterance, "private-password", "private-token", "private-user-id",
                   "private-callback", "private-signature", "synthetic-secret"):
        assert secret not in output
    assert f"[KAKAO] input_type={'voice' if voice else 'text'}" in output
    assert f"utterance_length={len(utterance)} user_present=True" in output
    assert "[CALLBACK EXISTS] True" in output
    assert "[CALLBACK HOST] callback.example.test" in output
    assert "utterance=" not in output
