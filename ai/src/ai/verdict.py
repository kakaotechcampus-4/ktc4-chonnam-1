"""최종 판정. LLM도 네트워크도 없는 순수함수입니다.

LLM이 제안한 신호는 후보일 뿐입니다. 근거가 실제 입력에 존재하는지
검증한 것만 채택합니다. RAG 유사도, 스캐너 점수, 주제 분류는 입력에
넣지 않습니다.
"""

from ai.types import (
    CheckState,
    DomainCheck,
    DomainMatch,
    EvidenceSource,
    FinalState,
    MessageAnalysis,
    Observations,
    ObservationStatus,
    PageState,
    ReasonCode,
    RiskSignal,
    Verdict,
)


def _accept_signals(
    masked_text: str,
    observations: Observations | None,
    risk_signals: list[RiskSignal],
) -> tuple[RiskSignal, ...]:
    found_checks = {
        key
        for key, check in (observations.checks if observations else {}).items()
        if check.state is CheckState.FOUND
    }
    static_signals = set(observations.static_risk_signals) if observations else set()

    accepted = []
    for item in risk_signals:
        ref = item.evidence_ref.strip()
        if not ref:
            continue
        if item.evidence_source is EvidenceSource.MESSAGE:
            if ref in masked_text:
                accepted.append(item)
        elif ref in found_checks or ref in static_signals:
            accepted.append(item)
    return tuple(accepted)


def decide(
    masked_text: str,
    extracted: MessageAnalysis,
    domain_check: DomainCheck,
    observations: Observations | None,
    risk_signals: list[RiskSignal],
) -> Verdict:
    """4상태와 그 근거를 반환합니다."""
    accepted = _accept_signals(masked_text, observations, risk_signals)

    def build(final_state: FinalState, reason_code: ReasonCode) -> Verdict:
        return Verdict(
            reason_code=reason_code,
            url=domain_check.checked_domain,
            official_domain=domain_check.official_domain,
            carrier_name=domain_check.carrier_name,
            final_state=final_state,
            accepted_signals=accepted,
        )

    page_state = observations.page_state if observations else None
    failed = observations is not None and observations.status is ObservationStatus.FAILED

    if domain_check.match is DomainMatch.NO_URL:
        return build(FinalState.INPUT_REQUIRED, ReasonCode.NO_URL)

    # 최종 URL이 유명 사이트로 튀었다. 그 도메인으로 판정하면 안 된다.
    if page_state is PageState.CLOAKED_SUSPECT:
        return build(FinalState.INCONCLUSIVE, ReasonCode.UNRESOLVED)

    # 도메인 대조는 접속 없이도 되므로 수집 실패보다 먼저 본다.
    if domain_check.match is DomainMatch.OFFICIAL:
        return build(FinalState.OFFICIAL_DOMAIN, ReasonCode.OFFICIAL_MATCH)

    if domain_check.match is DomainMatch.BRAND_MISMATCH:
        return build(FinalState.SMISHING_SUSPECTED, ReasonCode.LOOKALIKE)

    # 소진된 1회성 링크는 그 자체가 신호다. 검사가 전부 비어 있어도 안전이 아니다.
    if page_state is PageState.EXPIRED:
        return build(FinalState.SMISHING_SUSPECTED, ReasonCode.NOT_IN_WHITELIST)

    if accepted:
        return build(FinalState.SMISHING_SUSPECTED, ReasonCode.NOT_IN_WHITELIST)

    if page_state is PageState.UNREACHABLE or failed:
        return build(FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED)

    if domain_check.match is DomainMatch.UNRESOLVED:
        return build(FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED)

    return build(FinalState.INCONCLUSIVE, ReasonCode.NOT_IN_WHITELIST)
