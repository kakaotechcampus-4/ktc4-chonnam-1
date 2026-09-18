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
from ai.verdict import EXPIRED_EVIDENCE_REF, decide

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
        (DomainMatch.BRAND_MISMATCH, PageState.CLOAKED_SUSPECT, FinalState.SMISHING_SUSPECTED, ReasonCode.LOOKALIKE),
        (DomainMatch.NOT_REGISTERED, PageState.EXPIRED, FinalState.SMISHING_SUSPECTED, ReasonCode.EXPIRED_LINK),
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


def test_failed_collection_is_not_analyzable_even_when_page_rendered():
    # status=failed 항은 page_state 가 rendered 여도 걸려야 한다. 이 조합이 없으면
    # decide() 의 `or failed` 가 어떤 테스트에도 닿지 않아 회귀 보호가 없다.
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        observations(status=ObservationStatus.FAILED, page_state=PageState.RENDERED),
        [],
    )

    assert verdict.final_state is FinalState.NOT_ANALYZABLE
    assert verdict.reason_code is ReasonCode.UNRESOLVED


def test_cloaked_page_does_not_lend_its_observations_as_evidence(load_observations):
    # 클로킹이면 관측은 미끼 페이지를 본 것이다. 그 신호가 accepted_signals 에 실려
    # 나가면 Task 7 의 설명이 미끼 페이지를 근거로 서술하게 된다.
    cloaked = load_observations("form").model_copy(
        update={"page_state": PageState.CLOAKED_SUSPECT}
    )

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        cloaked,
        [signal("form_inputs")],
    )

    assert verdict.final_state is FinalState.INCONCLUSIVE
    assert verdict.accepted_signals == ()


def test_cloaked_page_keeps_message_evidence():
    cloaked = observations(page_state=PageState.CLOAKED_SUSPECT)

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        cloaked,
        [signal("한진택배", source=EvidenceSource.MESSAGE)],
    )

    assert [item.evidence_ref for item in verdict.accepted_signals] == ["한진택배"]


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


def test_short_message_evidence_is_rejected():
    # "요" 처럼 1~3글자는 한국어 문자 거의 어디에나 있어 근거가 되지 못한다.
    # 채택 신호가 그것뿐이면 판정이 오르면 안 된다.
    short = signal("요", source=EvidenceSource.MESSAGE)

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        observations(),
        [short],
    )

    assert verdict.accepted_signals == ()
    assert verdict.final_state is not FinalState.SMISHING_SUSPECTED


def test_message_evidence_at_minimum_length_is_still_accepted():
    # 4글자 이상이고 본문에 실제로 있으면 여전히 채택된다 (회귀 방지).
    good = signal("한진택배", source=EvidenceSource.MESSAGE)

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        observations(),
        [good],
    )

    assert [item.evidence_ref for item in verdict.accepted_signals] == ["한진택배"]
    assert verdict.final_state is FinalState.SMISHING_SUSPECTED


def test_official_domain_with_observation_signal_is_overturned(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.OFFICIAL, carrier_name="한진택배"),
        load_observations("form"),
        [signal("form_inputs", source=EvidenceSource.OBSERVATION)],
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED
    assert verdict.reason_code is ReasonCode.OFFICIAL_BUT_RISKY


def test_official_domain_survives_message_only_signal():
    # LLM 이 고른 메시지 근거만으로는 화이트리스트 일치를 뒤집지 못한다.
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.OFFICIAL, carrier_name="한진택배"),
        observations(),
        [signal("한진택배", source=EvidenceSource.MESSAGE)],
    )

    # 신호가 채택됐는데도 뒤집지 못했음을 보여야 한다. 이 단언이 없으면
    # 최소 길이를 올려 신호가 걸러져도 테스트가 통과해 아무것도 증명하지 못한다.
    assert [item.evidence_ref for item in verdict.accepted_signals] == ["한진택배"]
    assert verdict.final_state is FinalState.OFFICIAL_DOMAIN
    assert verdict.reason_code is ReasonCode.OFFICIAL_MATCH


def test_whitelist_reason_only_when_whitelist_was_actually_checked():
    # not_in_whitelist 는 "대조했고 목록에 없었다"는 주장이다. 대조 자체가
    # 실패한 UNRESOLVED 에서 이 코드가 나가면 하지 않은 확인을 단언하게 된다.
    #
    # 신호가 있는 판과 없는 판을 모두 돈다. 신호를 항상 넘기면 accepted 분기가
    # 먼저 걸려 not_in_whitelist 가 아예 나오지 않고, 단언이 한 번도 실행되지
    # 않은 채로 통과한다.
    seen = 0
    for match in DomainMatch:
        for page_state in PageState:
            for status in ObservationStatus:
                for signals in ([], [signal("한진택배", source=EvidenceSource.MESSAGE)]):
                    verdict = decide(
                        TEXT,
                        extracted(),
                        DomainCheck(match=match),
                        observations(status=status, page_state=page_state),
                        signals,
                    )
                    if verdict.reason_code is ReasonCode.NOT_IN_WHITELIST:
                        seen += 1
                        assert match is DomainMatch.NOT_REGISTERED

    # 위 단언이 한 번도 돌지 않으면 이 테스트는 아무것도 증명하지 못한다.
    assert seen > 0


def test_unresolved_domain_with_signal_does_not_claim_whitelist(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.UNRESOLVED),
        load_observations("form"),
        [signal("form_inputs")],
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED
    assert verdict.reason_code is ReasonCode.RISK_SIGNAL


def test_expired_link_is_carried_as_its_own_evidence(load_observations):
    # 만료가 판정을 올렸는데 accepted_signals 에 그 근거가 없으면, 설명은
    # 인용할 것이 없는 채로 위험하다고만 말하게 된다.
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("expired"),
        [],
    )

    assert verdict.final_state is FinalState.SMISHING_SUSPECTED
    assert verdict.reason_code is ReasonCode.EXPIRED_LINK
    assert [item.code for item in verdict.accepted_signals] == [
        RiskSignalCode.EXPIRED_LINK
    ]
    assert verdict.accepted_signals[0].evidence_ref == EXPIRED_EVIDENCE_REF


def test_expired_link_keeps_verified_message_evidence(load_observations):
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("expired"),
        [signal("한진택배", source=EvidenceSource.MESSAGE)],
    )

    assert [item.evidence_ref for item in verdict.accepted_signals] == [
        "한진택배",
        EXPIRED_EVIDENCE_REF,
    ]


def test_llm_cannot_forge_an_expiry_signal(load_observations):
    # form 픽스처에는 form_inputs 가 실제로 found 다. 코드만 expired_link 로
    # 바꿔 달면 실재하는 근거에 만료 라벨이 붙는다. 그 경로를 막는다.
    forged = RiskSignal(
        code=RiskSignalCode.EXPIRED_LINK,
        evidence_source=EvidenceSource.OBSERVATION,
        evidence_ref="form_inputs",
    )

    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        load_observations("form"),
        [forged],
    )

    assert verdict.accepted_signals == ()
    assert verdict.final_state is FinalState.INCONCLUSIVE


def test_official_domain_survives_an_expired_link():
    # 정상 택배사의 소진된 1회성 조회 링크다. 합성 신호가 공식 도메인 분기까지
    # 흘러가면 여기서 OFFICIAL_BUT_RISKY 로 뒤집혀 오탐이 된다.
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.OFFICIAL, carrier_name="한진택배"),
        observations(page_state=PageState.EXPIRED),
        [],
    )

    assert verdict.final_state is FinalState.OFFICIAL_DOMAIN
    assert verdict.reason_code is ReasonCode.OFFICIAL_MATCH


def test_not_run_collection_is_not_analyzable():
    # 격리 서버를 아예 돌리지 않은 상태다. 실패와 마찬가지로 "확인하지 못했다"
    # 이지 "문제없다"가 아니다.
    verdict = decide(
        TEXT,
        extracted(),
        DomainCheck(match=DomainMatch.NOT_REGISTERED),
        observations(status=ObservationStatus.NOT_RUN, page_state=PageState.RENDERED),
        [],
    )

    assert verdict.final_state is FinalState.NOT_ANALYZABLE
    assert verdict.reason_code is ReasonCode.UNRESOLVED


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
