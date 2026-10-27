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

# v3 §7.5 표 순서 (링크 줄은 통화에 없다)
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


def _stop_actions(codes: set[EvidenceCode]) -> list[StopAction]:
    out = [action for trigger, action in _STOP_TABLE if codes & trigger]
    return [*out, StopAction.HANG_UP][:_MAX_STOP]


def _checked_unchecked(
    *,
    transcript_ok: bool,
    number_checks: list[NumberCheck],
    similar: CaseSearchResult | None,
) -> tuple[list[CheckItem], list[Unchecked]]:
    checked: list[CheckItem] = []
    unchecked: list[Unchecked] = []

    if transcript_ok:
        checked.append(CheckItem.TRANSCRIPT)
        # 받아 적은 글이 실제 통화와 같은지는 확인할 수 없다
        unchecked.append(
            Unchecked(
                item=UncheckedItem.TRANSCRIPT_ACCURACY,
                reason=UncheckedReason.READ_FROM_VOICE,
            )
        )
    else:
        unchecked.append(
            Unchecked(item=UncheckedItem.TRANSCRIPT, reason=UncheckedReason.FAILED)
        )

    if number_checks:
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

    # U-5: 통화에는 링크 후보 조건이 없다
    if not (codes & _ANY_DEMAND) and not (
        codes & {_E.INSTITUTION_CLAIM, _E.ACQUAINTANCE_CLAIM}
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
        stop_actions=_stop_actions(codes),
        questions=questions,
        explanation=explain_call(conclusion, evidence),
        **common,
    )
