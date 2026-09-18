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
    RiskSignalCode,
    Verdict,
)


# 한국어 문자에서 3글자 이하 부분문자열은 우연히 일치하므로 근거 구실을 못 한다.
# (조사 "요", 마침표 "." 처럼 거의 모든 문자에 존재하는 1글자도 걸러진다.)
MIN_MESSAGE_EVIDENCE_CHARS = 4

# 만료 신호의 근거 참조. found 검사 키도 static_risk_signals 원소도 아니므로
# LLM 이 같은 참조를 제안해도 _accept_signals 의 검증을 통과하지 못한다.
EXPIRED_EVIDENCE_REF = "page_state:expired"

# decide() 만 발행할 수 있는 코드. LLM 이 제안하면 실재하는 다른 근거에 이
# 라벨을 붙여 만료를 날조할 수 있으므로 코드 단계에서 버린다.
SYNTHETIC_ONLY_CODES = frozenset({RiskSignalCode.EXPIRED_LINK})


def _expired_signal() -> RiskSignal:
    """만료 사실을 근거 목록에 싣는다.

    만료는 PageState 라서 _accept_signals 를 거치지 않는다. 합성하지 않으면
    판정을 올린 사실이 accepted_signals 에 없어, 설명이 인용할 근거가 없는데
    위험하다고만 말하게 된다.
    """
    return RiskSignal(
        code=RiskSignalCode.EXPIRED_LINK,
        evidence_source=EvidenceSource.OBSERVATION,
        evidence_ref=EXPIRED_EVIDENCE_REF,
    )


def _accept_signals(
    masked_text: str,
    observations: Observations | None,
    risk_signals: list[RiskSignal],
) -> tuple[RiskSignal, ...]:
    # 클로킹 의심이면 관측은 미끼 페이지를 본 것이다. 거기서 나온 신호는 이번
    # 링크의 근거가 아니므로 채택하지 않는다. 메시지 근거는 페이지와 무관하므로 남는다.
    cloaked = observations is not None and observations.page_state is PageState.CLOAKED_SUSPECT

    found_checks = set()
    static_signals = set()
    if observations is not None and not cloaked:
        found_checks = {
            key
            for key, check in observations.checks.items()
            if check.state is CheckState.FOUND
        }
        static_signals = set(observations.static_risk_signals)

    accepted = []
    for item in risk_signals:
        if item.code in SYNTHETIC_ONLY_CODES:
            continue
        ref = item.evidence_ref.strip()
        if not ref:
            continue
        if item.evidence_source is EvidenceSource.MESSAGE:
            if len(ref) >= MIN_MESSAGE_EVIDENCE_CHARS and ref in masked_text:
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
    observation_signals = tuple(
        item for item in accepted if item.evidence_source is EvidenceSource.OBSERVATION
    )

    def build(
        final_state: FinalState,
        reason_code: ReasonCode,
        signals: tuple[RiskSignal, ...] | None = None,
    ) -> Verdict:
        return Verdict(
            reason_code=reason_code,
            url=domain_check.checked_domain,
            official_domain=domain_check.official_domain,
            carrier_name=domain_check.carrier_name,
            final_state=final_state,
            accepted_signals=accepted if signals is None else signals,
        )

    page_state = observations.page_state if observations else None
    # 수집을 못 했거나 아예 돌리지 않았거나. 둘 다 "확인하지 못했다"이지
    # "문제없다"가 아니다.
    unavailable = observations is not None and observations.status in (
        ObservationStatus.FAILED,
        ObservationStatus.NOT_RUN,
    )

    if domain_check.match is DomainMatch.NO_URL:
        return build(FinalState.INPUT_REQUIRED, ReasonCode.NO_URL)

    # 등록 브랜드 사칭은 원본 URL의 도메인 대조 결과다. 페이지가 무엇을 보여줬든
    # 바뀌지 않으므로 클로킹보다 먼저 본다.
    if domain_check.match is DomainMatch.BRAND_MISMATCH:
        return build(FinalState.SMISHING_SUSPECTED, ReasonCode.LOOKALIKE)

    # 최종 URL이 유명 사이트로 튀었다. 진짜 페이지를 못 본 것이므로 그 도메인으로
    # 공식 확인을 주지 않는다.
    if page_state is PageState.CLOAKED_SUSPECT:
        return build(FinalState.INCONCLUSIVE, ReasonCode.UNRESOLVED)

    # 도메인 대조는 접속 없이도 되므로 수집 실패보다 먼저 본다.
    if domain_check.match is DomainMatch.OFFICIAL:
        # 공식 도메인도 오픈 리다이렉트나 계정 탈취로 위험한 페이지를 띄울 수 있다.
        # 격리 환경이 실제로 관측한 사실은 화이트리스트 일치보다 무겁다.
        # 메시지 근거는 LLM 이 고른 문자열일 뿐이라 여기서 판정을 뒤집지 못한다.
        if observation_signals:
            return build(FinalState.SMISHING_SUSPECTED, ReasonCode.OFFICIAL_BUT_RISKY)
        return build(FinalState.OFFICIAL_DOMAIN, ReasonCode.OFFICIAL_MATCH)

    # 소진된 1회성 링크는 그 자체가 신호다. 검사가 전부 비어 있어도 안전이 아니다.
    # 메시지 근거보다 앞에 둔다 — 격리 환경이 직접 관측한 사실이 LLM 이 고른
    # 문자열보다 무겁다. 합성 신호는 여기서만 더한다. 위의 공식 도메인 분기가
    # 보는 observation_signals 에 섞이면 정상 택배사의 소진된 조회 링크가
    # OFFICIAL_BUT_RISKY 로 뒤집힌다.
    if page_state is PageState.EXPIRED:
        return build(
            FinalState.SMISHING_SUSPECTED,
            ReasonCode.EXPIRED_LINK,
            accepted + (_expired_signal(),),
        )

    if accepted:
        return build(FinalState.SMISHING_SUSPECTED, ReasonCode.RISK_SIGNAL)

    if page_state is PageState.UNREACHABLE or unavailable:
        return build(FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED)

    if domain_check.match is DomainMatch.UNRESOLVED:
        return build(FinalState.NOT_ANALYZABLE, ReasonCode.UNRESOLVED)

    return build(FinalState.INCONCLUSIVE, ReasonCode.NOT_IN_WHITELIST)
