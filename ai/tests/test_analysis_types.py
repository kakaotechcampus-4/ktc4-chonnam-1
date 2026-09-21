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


from ai.types import (
    CheckResult,
    CheckState,
    DomainCheck,
    DomainMatch,
    EvidenceSource,
    FinalState,
    Observations,
    ObservationStatus,
    PageState,
    ReasonCode,
    RiskSignal,
    RiskSignalCode,
    SignalProposal,
    Verdict,
)


def test_observations_ignores_unknown_keys():
    obs = Observations.model_validate(
        {
            "status": "success",
            "source": "stub",
            "page_state": "rendered",
            "checks": {"permissions": {"state": "found", "items": ["READ_SMS"]}},
            "elapsed_ms": 9400,
            "display": {"headline": "무시되어야 한다"},
        }
    )

    assert obs.status is ObservationStatus.SUCCESS
    assert obs.checks["permissions"].state is CheckState.FOUND
    assert not hasattr(obs, "display")


def test_observations_defaults_are_empty_not_absent():
    obs = Observations.model_validate(
        {"status": "failed", "source": "stub", "page_state": "unreachable"}
    )

    assert obs.checks == {}
    assert obs.static_risk_signals == []
    assert obs.unchecked == []
    assert obs.input_url is None


def test_check_result_requires_explicit_state():
    with pytest.raises(ValidationError):
        CheckResult.model_validate({"items": []})


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


def test_domain_check_ignores_unknown_keys():
    check = DomainCheck.model_validate(
        {"match": "official", "checked_domain": "cjlogistics.com", "raw_payload": {}}
    )

    assert check.match is DomainMatch.OFFICIAL


def test_verdict_keeps_existing_construction():
    verdict = Verdict(reason_code=ReasonCode.NO_URL)

    assert verdict.final_state is None
    assert verdict.accepted_signals == ()


def test_verdict_carries_final_state_and_signals():
    signal = RiskSignal(
        code=RiskSignalCode.CREDENTIAL_REQUEST,
        evidence_source=EvidenceSource.OBSERVATION,
        evidence_ref="form_inputs",
    )
    verdict = Verdict(
        reason_code=ReasonCode.LOOKALIKE,
        final_state=FinalState.SMISHING_SUSPECTED,
        accepted_signals=(signal,),
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED
    assert verdict.accepted_signals[0].evidence_ref == "form_inputs"
