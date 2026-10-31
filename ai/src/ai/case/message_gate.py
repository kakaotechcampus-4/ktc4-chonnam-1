"""검증된 근거와 쌓인 답으로 링크 없는 문자 결론을 정하는 순수 함수 (v3 §7·§8 중 문자 줄).

LLM·네트워크·시계를 쓰지 않는다. 처음 분석할 때와 답을 받을 때 같은 함수를 다시 돈다.
L-3(기관 사칭)은 통화에만 쓴다(v3 §13 열린 항목 7). 문자 기관 사칭은 주의 갈래로 보낸다.
"""

from __future__ import annotations

from typing import Literal

from ai.case.actions import response_actions_for
from ai.case.explain import explain_message
from ai.case.types import (
    AttachedQuestion,
    CheckItem,
    ClaimView,
    Conclusion,
    Evidence,
    EvidenceCode,
    MessageAnswers,
    MessageCaseResult,
    OfficialContact,
    Origin,
    QuestionId,
    RejectedClaim,
    Scenario,
    StopAction,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VerifyAction,
)
from ai.types import AnalysisStatus, CaseSearchResult, FailureCode

_E = EvidenceCode

# v3 §7.4 위험 요구 (통화 게이트와 같다)
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
_ANY_DEMAND = _RISK_DEMANDS | {_E.MESSENGER_MOVE}
_CLAIMS = frozenset({_E.INSTITUTION_CLAIM, _E.ACQUAINTANCE_CLAIM})
# L-4 지인 사칭의 요구 짝
_L4_DEMANDS = frozenset({_E.MONEY_REQUEST, _E.GIFT_CARD_REQUEST, _E.AUTH_CODE_REQUEST})

# v3 §7.5 표 순서. 링크가 없어서 dont_click_link 는, 통화가 아니라서 hang_up 은 없다
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
Q1_OPTIONS = ["entered_info", "installed_app", "sent_money", "not_yet"]
Q2_OPTIONS = ["not_person", "is_person", "no_answer", "not_yet", "unknown"]
# unknown 은 Q1 선택지에서 뺐지만 들어오면 not_yet 과 같이 처리한다 (v3 §7.6)
Q1_ACCEPTED = frozenset({*Q1_OPTIONS, "unknown"})
# 이 두 답이 오면 Q2 를 다시 묻지 않는다
_Q2_SETTLED = frozenset({"not_person", "is_person"})


def _answer_evidence(answers: MessageAnswers) -> list[Evidence]:
    if answers.called_known_number == "not_person":
        code, floor = _E.USER_NOT_PERSON, True
    elif answers.called_known_number == "is_person":
        code, floor = _E.USER_IS_PERSON, False
    else:
        return []
    return [
        Evidence(
            code=code,
            origin=Origin.USER_ANSWER,
            derived_by="answer",
            counts_for_floor=floor,
        )
    ]


def _stop_actions(codes: set[EvidenceCode]) -> list[StopAction]:
    return [action for trigger, action in _STOP_TABLE if codes & trigger][:_MAX_STOP]


def _slim(similar: CaseSearchResult | None) -> CaseSearchResult | None:
    """matched_variant 는 사례 본문이라 결과에 싣지 않는다. id·유사도만 남긴다."""
    if similar is None:
        return None
    return CaseSearchResult(
        status=similar.status,
        matches=[m.model_copy(update={"matched_variant": ""}) for m in similar.matches],
    )


def _checked_unchecked(
    *,
    read_ran: bool,
    similar: CaseSearchResult | None,
    search_miss: UncheckedReason | None,
    answered: bool,
) -> tuple[list[CheckItem], list[Unchecked]]:
    checked: list[CheckItem] = []
    unchecked: list[Unchecked] = []

    if read_ran:
        checked.append(CheckItem.MESSAGE_TEXT)
    if search_miss is not None:
        reason: UncheckedReason | None = search_miss
    elif similar is None:
        reason = UncheckedReason.NOT_RUN
    elif similar.status == AnalysisStatus.FALLBACK or not similar.matches:
        # 검색은 했지만 붙일 사례가 없으면 확인한 것으로 세지 않는다 (FE 3절)
        reason = UncheckedReason.KB_EMPTY
    else:
        reason = None
    if reason is None:
        checked.append(CheckItem.SIMILAR_CASES)
    else:
        unchecked.append(Unchecked(item=UncheckedItem.SIMILAR_CASES, reason=reason))

    if answered:
        checked.append(CheckItem.USER_ANSWER)

    # 내세운 신분을 읽었다고 실제로 보낸 사람을 확인한 것은 아니다 (v3 §7.9)
    unchecked.append(
        Unchecked(item=UncheckedItem.ACTUAL_SENDER, reason=UncheckedReason.CANNOT_VERIFY)
    )
    return checked, unchecked


def decide_message(
    *,
    accepted: list[Evidence] | None,
    claims: ClaimView | None = None,
    rejected: list[RejectedClaim] | None = None,
    similar: CaseSearchResult | None = None,
    search_miss: UncheckedReason | None = None,
    official_contact: OfficialContact | None = None,
    answers: MessageAnswers | None = None,
    failures: list[FailureCode] | None = None,
    reading_status: Literal["ok", "fallback"] = "ok",
) -> MessageCaseResult:
    """accepted 가 None 이면 문자를 읽지 못한 것이다(not_analyzable)."""
    answers = answers or MessageAnswers()
    read_ran = accepted is not None
    q2 = answers.called_known_number

    common = {
        "similar_cases": _slim(similar),
        "rejected": list(rejected or []),
        "claims": claims or ClaimView(),
        "reading_status": reading_status,
        "failures": list(failures or []),
        "answers": answers,
    }

    if accepted is None:
        checked, unchecked = _checked_unchecked(
            read_ran=False, similar=similar, search_miss=search_miss, answered=False
        )
        return MessageCaseResult(
            scenario=Scenario.UNKNOWN,
            conclusion=Conclusion.NOT_ANALYZABLE,
            checked=checked,
            unchecked=unchecked,
            explanation=explain_message(Conclusion.NOT_ANALYZABLE, []),
            **common,
        )

    # 읽기 근거만 남기고 답 근거는 지금 답으로 다시 만든다
    base = [e for e in accepted if e.origin != Origin.USER_ANSWER]
    codes = {e.code for e in base}

    # U-5: 내세운 신분도 요구도 없으면 링크 없는 사칭 문자로 보지 않는다
    if not (codes & _CLAIMS) and not (codes & _ANY_DEMAND):
        checked, unchecked = _checked_unchecked(
            read_ran=True, similar=similar, search_miss=search_miss, answered=False
        )
        return MessageCaseResult(
            scenario=Scenario.UNKNOWN,
            conclusion=Conclusion.INPUT_REQUIRED,
            evidence=base,
            # 질문이 붙지 않은 결과에 온 Q1 답에도 대응을 고른다 (v3 §8)
            response_actions=response_actions_for(answers.already_acted, base, call=False),
            checked=checked,
            unchecked=unchecked,
            explanation=explain_message(Conclusion.INPUT_REQUIRED, base),
            **common,
        )

    acquaintance = _E.ACQUAINTANCE_CLAIM in codes
    # Q2 는 가족·지인이라고 한 문자에만 묻는다. 그 밖의 문자에 온 Q2 답은 쓰지 않는다
    q2_applies = acquaintance and bool(codes & (_RISK_DEMANDS | {_E.MESSENGER_MOVE}))
    if not acquaintance:
        q2 = None
    evidence = [*base, *_answer_evidence(answers.model_copy(update={"called_known_number": q2}))]

    # L-4: 지인 주장 + 돈·상품권·인증번호 요구 + 원래 번호로 통화하니 본인이 아님
    floors: list[str] = []
    if acquaintance and codes & _L4_DEMANDS and q2 == "not_person":
        floors.append("L-4")

    caution_before_answer = bool(
        codes & _RISK_DEMANDS
        or _E.INSTITUTION_CLAIM in codes
        or (acquaintance and codes & {_E.MESSENGER_MOVE, _E.CONTACT_BLOCKER})
    )
    if floors:
        conclusion, caution = Conclusion.SUSPECTED, False
    else:
        conclusion = Conclusion.INCONCLUSIVE
        # 예외: 본인이 맞다고 하면 caution 만 끈다. 결론을 '안전' 쪽으로 옮기지는 않는다 (v3 §7.4)
        caution = caution_before_answer and q2 != "is_person"
    basis_from_answer = bool(floors) or (q2 == "is_person" and caution_before_answer)

    questions: list[AttachedQuestion] = []
    if conclusion == Conclusion.SUSPECTED or caution:
        questions.append(
            AttachedQuestion(question_id=QuestionId.ALREADY_ACTED, option_ids=list(Q1_OPTIONS))
        )
    if q2_applies and q2 not in _Q2_SETTLED:
        questions.append(
            AttachedQuestion(
                question_id=QuestionId.CALLED_KNOWN_NUMBER, option_ids=list(Q2_OPTIONS)
            )
        )

    verify_actions: list[VerifyAction] = []
    if acquaintance:
        if q2 == "is_person":
            verify_actions = [VerifyAction.CHECK_ACCOUNT_HOLDER]
        elif q2 != "not_person":
            verify_actions = [VerifyAction.CALL_KNOWN_NUMBER]

    checked, unchecked = _checked_unchecked(
        read_ran=True,
        similar=similar,
        search_miss=search_miss,
        answered=q2 in _Q2_SETTLED,
    )

    return MessageCaseResult(
        scenario=Scenario.IMPERSONATION_NO_LINK,
        conclusion=conclusion,
        caution=caution,
        floors=floors,
        evidence=evidence,
        stop_actions=_stop_actions(codes),
        response_actions=response_actions_for(answers.already_acted, base, call=False),
        verify_actions=verify_actions,
        questions=questions,
        checked=checked,
        unchecked=unchecked,
        official_contact=official_contact,
        basis_from_answer=basis_from_answer,
        explanation=explain_message(conclusion, evidence),
        **common,
    )
