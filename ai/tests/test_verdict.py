import pytest

from ai.types import (
    AnalysisStatus,
    CheckState,
    DomainCheck,
    DomainMatch,
    EvidenceField,
    EvidenceSource,
    FinalState,
    MessageAnalysis,
    Observations,
    ObservationStatus,
    PageState,
    ReasonCode,
    RiskSignal,
    RiskSignalCode,
)
from ai.verdict import decide

TEXT = "한진택배 확인부탁합니다"


def extracted() -> MessageAnalysis:
    return MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        categories=[],
        claimed_sender=EvidenceField(value="한진택배", evidence="한진택배"),
        claimed_purpose=EvidenceField(),
        requested_actions=[],
        persuasion_signals=[],
    )


def observations(**overrides) -> Observations:
    base = {
        "status": ObservationStatus.SUCCESS,
        "source": "stub",
        "page_state": PageState.RENDERED,
        "checks": {},
        "static_risk_signals": [],
        "unchecked": [],
    }
    base.update(overrides)
    return Observations.model_validate(base)


def signal(ref: str, source=EvidenceSource.OBSERVATION) -> RiskSignal:
    return RiskSignal(
        code=RiskSignalCode.CREDENTIAL_REQUEST,
        evidence_source=source,
        evidence_ref=ref,
    )


@pytest.mark.parametrize(
    "match,page_state,expected_state,expected_reason",
    [
        (DomainMatch.NO_URL, PageState.RENDERED, FinalState.INPUT_REQUIRED, ReasonCode.NO_URL),
        (DomainMatch.OFFICIAL, PageState.RENDERED, FinalState.OFFICIAL_DOMAIN, ReasonCode.OFFICIAL_MATCH),
        (DomainMatch.BRAND_MISMATCH, PageState.RENDERED, FinalState.SMISHING_SUSPECTED, ReasonCode.LOOKALIKE),
        (DomainMatch.NOT_REGISTERED, PageState.RENDERED, FinalState.INCONCLUSIVE, ReasonCode.NOT_IN_WHITELIST),
        (DomainMatch.UNRESOLVED, PageState.RENDERED, FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED),
        (DomainMatch.OFFICIAL, PageState.CLOAKED_SUSPECT, FinalState.INCONCLUSIVE, ReasonCode.UNRESOLVED),
        (DomainMatch.NOT_REGISTERED, PageState.EXPIRED, FinalState.SMISHING_SUSPECTED, ReasonCode.NOT_IN_WHITELIST),
        (DomainMatch.NOT_REGISTERED, PageState.UNREACHABLE, FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED),
    ],
)
def test_state_table(match, page_state, expected_state, expected_reason):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=match),
        observations(page_state=page_state),
        [],
    )

    assert verdict.final_state is expected_state
    assert verdict.reason_code is expected_reason


def test_official_domain_survives_unreachable_page():
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.OFFICIAL),
        observations(status=ObservationStatus.FAILED, page_state=PageState.UNREACHABLE),
        [],
    )

    assert verdict.final_state is FinalState.OFFICIAL_DOMAIN


def test_signal_backed_by_found_check_is_accepted(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("form"),
        [signal("form_inputs")],
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED
    assert len(verdict.accepted_signals) == 1


def test_signal_backed_by_static_risk_signal_is_accepted(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("apk"),
        [
            RiskSignal(
                code=RiskSignalCode.PACKER_DETECTED,
                evidence_source=EvidenceSource.OBSERVATION,
                evidence_ref="commercial_packer",
            )
        ],
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED


def test_signal_referencing_unknown_check_is_dropped(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("apk"),
        [signal("iocs")],
    )

    assert verdict.accepted_signals == ()
    assert verdict.final_state is FinalState.INCONCLUSIVE


def test_signal_referencing_absent_check_is_dropped(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("benign"),
        [signal("form_inputs")],
    )

    assert verdict.accepted_signals == ()


def test_signal_referencing_missing_key_is_dropped(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("benign"),
        [signal("존재하지_않는_검사")],
    )

    assert verdict.accepted_signals == ()


def test_message_signal_requires_verbatim_quote():
    good = signal("한진택배", source=EvidenceSource.MESSAGE)
    bad = signal("우체국택배", source=EvidenceSource.MESSAGE)

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        observations(),
        [good, bad],
    )

    assert [item.evidence_ref for item in verdict.accepted_signals] == ["한진택배"]


def test_one_broken_signal_does_not_drop_the_others(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("apk"),
        [signal("iocs"), signal("permissions")],
    )

    assert [item.evidence_ref for item in verdict.accepted_signals] == ["permissions"]


def test_missing_observations_drops_observation_signals():
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        None,
        [signal("form_inputs")],
    )

    assert verdict.accepted_signals == ()
    assert verdict.final_state is FinalState.INCONCLUSIVE


def test_carrier_name_is_carried_through():
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(
            match=DomainMatch.OFFICIAL,
            carrier_name="한진택배",
            official_domain="hanjin.com",
            checked_domain="hanjin.com",
        ),
        observations(),
        [],
    )

    assert verdict.carrier_name == "한진택배"
    assert verdict.official_domain == "hanjin.com"
    assert verdict.url == "hanjin.com"
