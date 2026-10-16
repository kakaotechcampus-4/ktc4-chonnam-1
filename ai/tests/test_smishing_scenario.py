"""택배 사칭 한 사례를 기존 공개 AI API로 끝까지 처리한다.

SDK 응답만 대체한다. 추출 검증·사례 검색·요구 행동 분류·근거 검증·
최종 결과 조립은 실제 코드를 사용한다. 실제 모델 품질 검증은 별도다.
"""

from types import SimpleNamespace

import pytest

from ai.pipeline import analyze_message_part, finalize_analysis, warm_up_case_search
from ai.types import (
    AnswerState, Brand, CategoryCode, CategoryEvidence, DomainMatch,
    EvidenceField, EvidenceSource, ExtractedMessage, MessageDoubt,
    RiskSignal, RiskSignalCode, SignalProposal, UrlAnalysis,
)


TEXT = "[CJ대한통운] 주소 불일치로 배송이 보류되었습니다. 배송 확인 앱을 설치해 주세요."
REQUEST = "배송 확인 앱을 설치해 주세요"


def sdk_response(parsed):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(parsed=parsed, refusal=None),
    )])


@pytest.mark.parametrize("signal_outcome", ["grounded", "invented", "timeout"])
async def test_parcel_install_message_preserves_facts_and_verifies_evidence(
    signal_outcome, make_parse_client,
):
    warm_up_case_search()
    extracted = ExtractedMessage(
        categories=[CategoryEvidence(code=CategoryCode.ADDRESS_CORRECTION,
                                     evidence="주소 불일치")],
        claimed_sender=EvidenceField(value="CJ대한통운", evidence="CJ대한통운"),
        claimed_purpose=EvidenceField(value="배송 보류", evidence="배송이 보류되었습니다"),
        requested_actions=[EvidenceField(value="앱 설치", evidence=REQUEST)],
    )
    proposal = SignalProposal(signals=[RiskSignal(
        code=RiskSignalCode.INSTALL_PROMPT,
        evidence_source=EvidenceSource.MESSAGE,
        evidence_ref=REQUEST if signal_outcome == "grounded" else "다른 원격 앱을 설치하세요",
    )])
    client, _ = make_parse_client(side_effect=[
        sdk_response(extracted),
        TimeoutError() if signal_outcome == "timeout" else sdk_response(proposal),
    ])

    message = await analyze_message_part(TEXT, client=client, model="test")
    result = await finalize_analysis(
        UrlAnalysis(final_url="https://parcel-test.example/", domain="parcel-test.example",
                    official=DomainMatch.BRAND_MISMATCH),
        message=message, page=None,
    )

    assert result.message.brand is Brand.CJ_LOGISTICS
    assert MessageDoubt.APP_INSTALL in {item.value for item in result.message.details.doubts}
    assert "CJ대한통운" in result.message.details.reason.text
    assert all(item.evidence in TEXT for item in result.message.details.doubts)
    assert result.env.answer is AnswerState.NOT_RUN
    assert result.result is False
    if signal_outcome == "grounded":
        assert result.message.answer is AnswerState.RISK_FOUND
        assert [(item.code, item.evidence) for item in result.message.details.signals] == [
            (RiskSignalCode.INSTALL_PROMPT, REQUEST),
        ]
    else:
        assert result.message.details.signals == []
        if signal_outcome == "timeout":
            assert result.message.answer is AnswerState.PARTIAL
            assert "timeout" in result.message.details.reason.failures
        else:
            assert result.message.answer is AnswerState.NO_RISK_FOUND
