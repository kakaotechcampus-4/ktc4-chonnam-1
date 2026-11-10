import json

import pytest
from pydantic import ValidationError

from ai.types import (
    AnalysisStatus,
    CategoryCode,
    CategoryEvidence,
    EvidenceField,
    ExtractedMessage,
    FailureCode,
    MessageAnalysis,
)


EXPECTED_KEYS = {
    "analysis_status",
    "categories",
    "claimed_sender",
    "claimed_purpose",
    "requested_actions",
    "persuasion_signals",
    "urls",
}


def test_message_analysis_has_fixed_top_level_shape():
    result = MessageAnalysis(
        analysis_status=AnalysisStatus.FALLBACK,
        categories=[],
        claimed_sender=EvidenceField(),
        claimed_purpose=EvidenceField(),
        requested_actions=[],
        persuasion_signals=[],
    )

    assert set(result.model_dump(mode="json")) == EXPECTED_KEYS
    assert result.claimed_sender.value is None
    assert result.claimed_sender.evidence is None


@pytest.mark.parametrize("failure", list(FailureCode))
def test_message_failure_is_internal_and_preserves_serialized_keys(failure):
    result = MessageAnalysis(
        analysis_status=AnalysisStatus.FALLBACK, failure=failure)
    assert result.failure is failure
    assert set(result.model_dump()) == EXPECTED_KEYS
    assert set(result.model_dump(mode="json")) == EXPECTED_KEYS
    assert set(json.loads(result.model_dump_json())) == EXPECTED_KEYS
    assert set(MessageAnalysis.model_json_schema(mode="serialization")["properties"]) == EXPECTED_KEYS


def test_completed_message_cannot_carry_failure():
    with pytest.raises(ValidationError):
        MessageAnalysis(
            analysis_status=AnalysisStatus.COMPLETED,
            failure=FailureCode.TIMEOUT)


def test_legacy_message_construction_still_works():
    assert MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED).failure is None
    assert MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK).failure is None


def test_evidence_field_requires_value_and_evidence_together():
    with pytest.raises(ValidationError):
        EvidenceField(value="sender", evidence=None)


def test_other_category_requires_custom_label():
    with pytest.raises(ValidationError):
        CategoryEvidence(code=CategoryCode.OTHER, custom_label=None, evidence="text")


def test_known_category_rejects_custom_label():
    with pytest.raises(ValidationError):
        CategoryEvidence(
            code=CategoryCode.DELIVERY, custom_label="custom", evidence="text"
        )


def test_contract_drops_unknown_fields_so_llm_cannot_add_a_verdict():
    # LLM 이 판정 칸을 지어내 붙여도 계약에 실리지 않는다. 답 전체는 버리지 않는다(#145)
    extracted = ExtractedMessage.model_validate({"risk_verdict": "malicious"})
    assert "risk_verdict" not in extracted.model_dump()
    with pytest.raises(ValidationError):
        MessageAnalysis.model_validate({"analysis_status": "completed", "risk_verdict": "malicious"})


def test_category_taxonomy_matches_approved_design():
    assert {code.value for code in CategoryCode} == {
        "delivery",
        "address_correction",
        "payment",
        "penalty",
        "card_or_account",
        "public_refund",
        "public_support",
        "acquaintance_impersonation",
        "invitation",
        "obituary",
        "prize_or_event",
        "health_check",
        "telecom_refund",
        "account_security",
        "other",
        "unknown",
    }


def test_other_category_preserves_evidence_backed_custom_label():
    item = CategoryEvidence(
        code=CategoryCode.OTHER,
        custom_label="parcel storage scam",
        evidence="Your parcel is held in storage.",
    )

    assert item.model_dump(mode="json") == {
        "code": "other",
        "custom_label": "parcel storage scam",
        "evidence": "Your parcel is held in storage.",
    }


from ai.types import (
    RiskSignal,
    SignalProposal,
)


def test_risk_signal_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RiskSignal.model_validate(
            {
                "code": "install_prompt",
                "evidence_source": "observation",
                "evidence_ref": "download_links",
                "confidence": 0.9,
            }
        )


def test_signal_proposal_defaults_to_empty():
    assert SignalProposal().signals == []


