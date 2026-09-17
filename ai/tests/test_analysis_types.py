import pytest
from pydantic import ValidationError

from ai.types import (
    AnalysisStatus,
    CategoryCode,
    CategoryEvidence,
    EvidenceField,
    ExtractedMessage,
    MessageAnalysis,
)


EXPECTED_KEYS = {
    "analysis_status",
    "categories",
    "claimed_sender",
    "claimed_purpose",
    "requested_actions",
    "persuasion_signals",
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


def test_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ExtractedMessage.model_validate({"risk_verdict": "malicious"})


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
