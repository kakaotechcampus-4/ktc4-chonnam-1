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
from ai.types import AnalysisStatus, CaseSearchResult, FailureCode

_E = EvidenceCode

# L-3 기관 사칭의 짝. counts_for_floor 가 아니라 이 목록으로 하한을 정한다.
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

# v3 §7.4 위험 요구
_RISK_DEMANDS = frozenset(
    {
        _E.MONEY_REQUEST,
        _E.CASH_DELIVERY_REQUEST,
        _E.GIFT_CARD_REQUEST,
        _E.AUTH_CODE_REQUEST,
        _E.INFO_REQUEST,
        _E.APP_INSTALL_REQUEST,
        _E.REMOTE_APP_MENTION,
        _E.CALL_REQUEST,
    }
)

# U-5 에서 "요구가 있다"고 보는 코드
_ANY_DEMAND = _RISK_DEMANDS | {_E.MESSENGER_MOVE}

# v3 §7.5 표 순서. 링크 줄(dont_click_link)은 말로 부른 주소가 있을 때만 맨 앞에 붙는다
_STOP_TABLE: list[tuple[frozenset[EvidenceCode], StopAction]] = [
    (frozenset({_E.INFO_REQUEST}), StopAction.DONT_ENTER_INFO),
    (
        frozenset({_E.APP_INSTALL_REQUEST, _E.REMOTE_APP_MENTION}),
        StopAction.DONT_INSTALL_APP,
    ),
    (
        frozenset({_E.MONEY_REQUEST, _E.CASH_DELIVERY_REQUEST}),
        StopAction.DONT_SEND_MONEY,
    ),
    (frozenset({_E.GIFT_CARD_REQUEST}), StopAction.DONT_SEND_GIFT_CARD),
    (frozenset({_E.AUTH_CODE_REQUEST}), StopAction.DONT_SHARE_AUTH_CODE),
    (frozenset({_E.CALL_REQUEST}), StopAction.DONT_CALL_NUMBER_IN_MESSAGE),
]
_MAX_STOP = 3

# 링크가 없어서 clicked_link 는 없다 (v3 §7.6)
_Q1_OPTIONS = ["entered_info", "installed_app", "sent_money", "not_yet"]


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
    out = [action for trigger, action in _STOP_TABLE if codes & trigger]
    if link_spoken:
        out.insert(0, StopAction.DONT_CLICK_LINK)
    # 통화에서는 끊기가 가장 중요한 행동이라 상한에 잘리지 않게 둘만 고르고 끝에 붙인다
    return [*out[: _MAX_STOP - 1], StopAction.HANG_UP]


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


def _slim(similar: CaseSearchResult | None) -> CaseSearchResult | None:
    """matched_variant 는 대본 통째라 결과에 싣지 않는다. id·유사도만 남긴다."""
    if similar is None:
        return None
    return CaseSearchResult(
        status=similar.status,
        matches=[m.model_copy(update={"matched_variant": ""}) for m in similar.matches],
    )


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
        "similar_cases": _slim(similar),
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

    # 말로 부른 주소나 "주소로 들어가라"는 요구(R16). 링크는 조사하지 않는다
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

    # U-5: 통화에는 링크 후보 조건이 없지만 말로 부른 링크는 요구로 센다
    if (
        not link_spoken
        and not (codes & _ANY_DEMAND)
        and not (codes & {_E.INSTITUTION_CLAIM, _E.ACQUAINTANCE_CLAIM})
    ):
        return VoiceCaseResult(
            conclusion=Conclusion.INPUT_REQUIRED,
            explanation=explain_call(Conclusion.INPUT_REQUIRED, evidence),
            **common,
        )

    # L-3. U-4: 화자 분리가 실패하면 누구의 말인지 몰라 쓰지 않는다
    floors: list[str] = []
    if not verify.speaker_split_failed and (
        (_E.INSTITUTION_CLAIM in codes and codes & _L3_DEMANDS)
        or (_E.ACQUAINTANCE_CLAIM in codes and _E.MONEY_REQUEST in codes)
    ):
        floors.append("L-3")

    caution = False
    if floors:
        conclusion = Conclusion.SUSPECTED
    else:
        conclusion = Conclusion.INCONCLUSIVE
        caution = bool(
            codes & _RISK_DEMANDS
            or verify.speaker_split_failed
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
                question_id=QuestionId.ALREADY_ACTED, option_ids=list(_Q1_OPTIONS)
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
