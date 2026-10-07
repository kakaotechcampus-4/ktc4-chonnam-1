"""Grounded message claims reach the real result card without certifying a sender."""

import pytest

from ai.pipeline.results import build_message_part
from ai.types import (
    AnalysisStatus, CaseSearchResult, EvidenceField, FailureCode,
    MessageAnalysis, SignalAnalysis,
)
from services.result_card_renderer import render_result_card


@pytest.mark.parametrize("search_failed", [False, True])
@pytest.mark.parametrize("grounded", [False, True])
def test_waste_message_claims_reach_card_only_with_current_evidence(grounded, search_failed):
    text = "[청소행정과] 쓰레기 무단투기 신고가 접수되었습니다. 아래 링크에서 민원 내용을 확인하세요."
    sender_quote = "청소행정과" if grounded else "강동구청"
    purpose_quote = "쓰레기 무단투기 신고가 접수되었습니다." if grounded else "과태료 부과 확정"
    action_quote = "아래 링크에서 민원 내용을 확인하세요." if grounded else "앱을 설치하세요"
    extracted = MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        # The explanation must use the quote, not an unsupported interpretation.
        claimed_sender=EvidenceField(value="강동구청", evidence=sender_quote),
        claimed_purpose=EvidenceField(value="과태료 부과 확정", evidence=purpose_quote),
        requested_actions=[EvidenceField(value="민원 확인", evidence=action_quote)],
    )
    part = build_message_part(
        text, extracted,
        CaseSearchResult(status=AnalysisStatus.FALLBACK if search_failed else AnalysisStatus.COMPLETED),
        SignalAnalysis(status=AnalysisStatus.COMPLETED),
        failure=FailureCode.TIMEOUT if search_failed else None,
    )
    card = render_result_card({
        "url": {"official": "not_registered"},
        "message": part.model_dump(mode="json"),
        "env": {"answer": "not_run"},
    })["template"]["outputs"][0]["textCard"]
    for output in (part.details.reason.text, card["description"]):
        for quote in ("청소행정과", "쓰레기 무단투기 신고가 접수되었습니다.", "아래 링크에서 민원 내용을 확인하세요."):
            assert (quote in output) is grounded
        assert "강동구청" not in output
        assert "과태료 부과 확정" not in output
    assert part.details.signals == []
    assert part.answer.value == ("partial" if search_failed else "no_risk_found")
    assert "실제 발신자" in card["description"]
    assert "확인하지 못" in card["description"]
    assert "택배사" not in card["description"]
    assert "{{" not in card["description"]
    if search_failed:
        assert part.details.reason.failures == [FailureCode.TIMEOUT]
        assert "문자 분석을 완료하지 못" in card["description"]


def test_fallback_extraction_does_not_present_model_sender_as_a_verified_claim():
    part = build_message_part(
        "[청소행정과] 처리 안내입니다.",
        MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK,
            claimed_sender=EvidenceField(value="청소행정과", evidence="청소행정과")),
        CaseSearchResult(status=AnalysisStatus.COMPLETED),
        SignalAnalysis(status=AnalysisStatus.COMPLETED),
    )
    assert "청소행정과" not in part.details.reason.text
    assert part.answer.value == "partial"


@pytest.mark.parametrize("message_answer", ["no_risk_found", "partial"])
def test_long_reason_keeps_card_limit_and_verification_guidance(message_answer):
    reason = "문자가 내세운 기관: '청소행정과'. " + "긴 설명입니다. " * 100
    result = {
        "url": {"official": "not_registered"},
        "message": {"answer": message_answer, "details": {"reason": {"text": reason}}},
        "env": {"answer": "failed"},
    }
    card = render_result_card(result)["template"]["outputs"][0]["textCard"]
    assert len(card["description"]) <= 400
    assert "청소행정과" in card["description"]
    assert "…" in card["description"]
    assert "공식 홈페이지" in card["description"]
    assert "실제 발신자" in card["description"]
    assert "페이지를 열어보려 했지만 열지 못" in card["description"]
    assert "위험 근거는 찾지 못" not in card["description"]
    assert "{{" not in card["description"]
    assert result["message"]["details"]["reason"]["text"] == reason
    if message_answer == "partial":
        assert "문자 분석을 완료하지 못" in card["description"]


def test_missing_message_reason_uses_a_neutral_fallback():
    card = render_result_card({"url": {"official": "unresolved"}})["template"]["outputs"][0]["textCard"]
    assert "문자 분석 결과를 전달받지 못" in card["description"]
    assert "{{" not in card["description"]
    assert "위험 근거는 찾지 못" not in card["description"]
