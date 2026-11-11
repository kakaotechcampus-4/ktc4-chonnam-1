"""검증된 근거로 통화 결론을 정하는 순수 함수 (v3 §7 중 통화 줄만).

LLM·네트워크·시계를 쓰지 않는다. 모델이 낸 주장은 verify 를 통과한 것만 들어온다.
"""

from __future__ import annotations

from typing import Literal

from ai.case.explain import explain_call
from ai.case.types import (
    AttachedQuestion,
    CheckItem,
    Conclusion,
    DemandKind,
    Evidence,
    EvidenceCode,
    NumberCheck,
    Origin,
    QuestionId,
    Scenario,
    StopAction,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VoiceCaseResult,
)
from ai.case.verify import VerifyResult
from ai.links import spoken_link
from ai.case.rules import MAX_STOP, Q1_OPTIONS, RISK_DEMANDS, STOP_TABLE, slim
from ai.types import AnalysisStatus, CaseSearchResult, FailureCode

_E = EvidenceCode

# L-3 기관 사칭의 짝. counts_for_floor 인 근거 중 이 목록에 든 것만 짝이 된다(gift_card 는 빠진다).
_L3_DEMANDS = frozenset(
    {
        _E.MONEY_REQUEST,
        _E.APP_INSTALL_REQUEST,
        _E.REMOTE_APP_MENTION,
        _E.INFO_REQUEST,
        _E.AUTH_CODE_REQUEST,
        _E.CASH_DELIVERY_REQUEST,
    }
)


# U-5 에서 "요구가 있다"고 보는 코드
_ANY_DEMAND = RISK_DEMANDS | {_E.MESSENGER_MOVE}


def _number_evidence(number_checks: list[NumberCheck]) -> list[Evidence]:
    """공식이 아닌 번호는 보조 근거다. 하한에는 쓰지 않고, 하나만 싣는다."""
    return [
        Evidence(
            code=_E.UNOFFICIAL_NUMBER,
            origin=Origin.TOOL,
            quote=c.number,
            derived_by="code",
            counts_for_floor=False,
        )
        for c in number_checks
        if c.result == "not_official"
    ][:1]


def _stop_actions(codes: set[EvidenceCode], link_spoken: bool = False) -> list[StopAction]:
    out = [action for trigger, action in STOP_TABLE if codes & trigger]
    if link_spoken:
        out.insert(0, StopAction.DONT_CLICK_LINK)
    # 통화에서는 끊기가 가장 중요한 행동이라 상한에 잘리지 않게 둘만 고르고 끝에 붙인다
    return [*out[: MAX_STOP - 1], StopAction.HANG_UP]


def _checked_unchecked(
    *,
    transcript_ok: bool,
    read_ran: bool,
    number_checks: list[NumberCheck],
    similar: CaseSearchResult | None,
) -> tuple[list[CheckItem], list[Unchecked]]:
    checked: list[CheckItem] = []
    unchecked: list[Unchecked] = []

    if transcript_ok and read_ran:
        checked.append(CheckItem.TRANSCRIPT)
        # 받아 적은 글이 실제 통화와 같은지는 확인할 수 없다
        unchecked.append(
            Unchecked(
                item=UncheckedItem.TRANSCRIPT_ACCURACY,
                reason=UncheckedReason.READ_FROM_VOICE,
            )
        )
    elif transcript_ok:
        # 대본은 있으나 읽기·검증이 돌지 않았다
        unchecked.append(
            Unchecked(item=UncheckedItem.TRANSCRIPT, reason=UncheckedReason.NOT_RUN)
        )
    else:
        unchecked.append(
            Unchecked(item=UncheckedItem.TRANSCRIPT, reason=UncheckedReason.FAILED)
        )

    # org_not_listed 는 대조가 되지 않은 것이라 확인한 것으로 세지 않는다
    if any(c.result in ("official", "not_official") for c in number_checks):
        checked.append(CheckItem.PHONE_NUMBER)
    else:
        unchecked.append(
            Unchecked(
                item=UncheckedItem.PHONE_NUMBER,
                reason=UncheckedReason.NOT_APPLICABLE,
            )
        )

    if similar is None:
        reason = UncheckedReason.NOT_RUN
    elif similar.status == AnalysisStatus.FALLBACK:
        reason = UncheckedReason.KB_EMPTY
    else:
        reason = None
    if reason is None:
        checked.append(CheckItem.SIMILAR_CASES)
    else:
        unchecked.append(Unchecked(item=UncheckedItem.SIMILAR_CASES, reason=reason))

    # 내세운 신분을 읽었다고 실제 발신자를 확인한 것은 아니다
    unchecked.append(
        Unchecked(
            item=UncheckedItem.ACTUAL_SENDER, reason=UncheckedReason.CANNOT_VERIFY
        )
    )
    return checked, unchecked


def decide_call(
    *,
    transcript_empty: bool,
    verify: VerifyResult | None,
    number_checks: list[NumberCheck],
    similar: CaseSearchResult | None,
    failures: list[FailureCode],
    reading_status: Literal["ok", "fallback"] = "ok",
) -> VoiceCaseResult:
    checked, unchecked = _checked_unchecked(
        transcript_ok=not transcript_empty,
        read_ran=verify is not None,
        number_checks=number_checks,
        similar=similar,
    )
    common = {
        "scenario": Scenario.VOICE_PHISHING,
        "number_checks": number_checks,
        "similar_cases": slim(similar),
        "checked": checked,
        "unchecked": unchecked,
        "reading_status": reading_status,
        "failures": list(failures),
    }

    # U-6
    if transcript_empty or verify is None:
        return VoiceCaseResult(
            conclusion=Conclusion.NOT_ANALYZABLE,
            explanation=explain_call(Conclusion.NOT_ANALYZABLE, []),
            **common,
        )

    evidence = [*verify.accepted, *_number_evidence(number_checks)]
    codes = {e.code for e in verify.accepted}
    unofficial = any(e.code == _E.UNOFFICIAL_NUMBER for e in evidence)
    common.update(evidence=evidence, claims=verify.claims, rejected=verify.rejected)
    # 읽기에서 함께 뽑은 주소를 BE 가 조사할 수 있게 넘긴다 (ADR 0007)
    common["links"] = [
        link for host in verify.spoken_hosts if (link := spoken_link(host)) is not None
    ]

    # 말로 부른 주소나 "주소로 들어가라"는 요구(R16). 여기서는 링크를 조사하지 않는다
    link_spoken = bool(verify.spoken_hosts) or any(
        d.kind == DemandKind.VISIT_LINK for d in verify.claims.demands
    )
    if link_spoken:
        common["unchecked"] = [
            *common["unchecked"],
            Unchecked(
                item=UncheckedItem.LINK_SPELLING, reason=UncheckedReason.READ_FROM_VOICE
            ),
        ]

    # U-5: 통화에는 링크 후보 조건이 없지만 말로 부른 링크는 요구로 센다.
    # 읽기가 실패해 어휘표로 떨어졌으면 "주장이 없다"고 단정할 수 없으므로
    # 입력 요청 대신 아래의 판단 보류(INCONCLUSIVE)로 보낸다
    if (
        reading_status != "fallback"
        and not link_spoken
        and not (codes & _ANY_DEMAND)
        and not (codes & {_E.INSTITUTION_CLAIM, _E.ACQUAINTANCE_CLAIM})
        # 꼴 때문에 버린 신원·요구가 있으면 "말이 없다"고 단정하지 않는다
        and not any(
            r.reason == "self_side" and r.kind in ("identity", "demand")
            for r in verify.rejected
        )
    ):
        return VoiceCaseResult(
            conclusion=Conclusion.INPUT_REQUIRED,
            explanation=explain_call(Conclusion.INPUT_REQUIRED, evidence),
            **common,
        )

    # L-3. 화자를 나누지 않으므로(ADR 0008) 꼴까지 맞은 근거(counts_for_floor)만 쓴다:
    # 자기소개 꼴의 신원 주장 + 부탁 꼴의 요구. 받는 사람이 되풀이한 말은 verify 가 이미 뺐다
    floor_codes = {e.code for e in verify.accepted if e.counts_for_floor}
    floors: list[str] = []
    if (_E.INSTITUTION_CLAIM in floor_codes and floor_codes & _L3_DEMANDS) or (
        _E.ACQUAINTANCE_CLAIM in floor_codes and _E.MONEY_REQUEST in floor_codes
    ):
        floors.append("L-3")

    caution = False
    if floors:
        conclusion = Conclusion.SUSPECTED
    else:
        conclusion = Conclusion.INCONCLUSIVE
        caution = bool(
            codes & RISK_DEMANDS
            or unofficial
            or link_spoken
            or _E.INSTITUTION_CLAIM in codes
            or (
                _E.ACQUAINTANCE_CLAIM in codes
                and codes & {_E.MESSENGER_MOVE, _E.CONTACT_BLOCKER}
            )
        )

    questions: list[AttachedQuestion] = []
    if conclusion == Conclusion.SUSPECTED or caution:
        questions.append(
            AttachedQuestion(
                question_id=QuestionId.ALREADY_ACTED, option_ids=list(Q1_OPTIONS)
            )
        )

    return VoiceCaseResult(
        conclusion=conclusion,
        caution=caution,
        floors=floors,
        stop_actions=_stop_actions(codes, link_spoken),
        questions=questions,
        explanation=explain_call(conclusion, evidence),
        **common,
    )
