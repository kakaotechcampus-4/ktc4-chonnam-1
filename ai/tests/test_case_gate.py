"""decide_call·response_actions_for·explain_call: v3 §7 (통화)."""

from __future__ import annotations

import pytest

from ai.case.actions import apply_call_answers, default_response_actions, response_actions_for
from ai.case.explain import LEADS, TEMPLATES, explain_call
from ai.case.gate import decide_call
from ai.case.types import (
    CheckItem,
    ClaimView,
    Conclusion,
    Evidence,
    EvidenceCode as E,
    NumberCheck,
    Origin,
    ResponseAction as R,
    Scenario,
    StopAction as S,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
)
from ai.case.verify import VerifyResult
from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult, FailureCode


def _ev(code: E, quote: str = "인용 문장입니다") -> Evidence:
    return Evidence(
        code=code,
        origin=Origin.TRANSCRIPT,
        quote=quote,
        derived_by="llm",
        counts_for_floor=True,
    )


def _verify(
    *codes: E, split_failed: bool = False, spoken_hosts: list[str] | None = None
) -> VerifyResult:
    return VerifyResult(
        accepted=[_ev(c) for c in codes],
        rejected=[],
        other_speaker="화자2",
        speaker_split_failed=split_failed,
        spoken_hosts=spoken_hosts or [],
        claims=ClaimView(),
    )


def _decide(verify=None, *, empty=False, numbers=None, similar=None, failures=None):
    return decide_call(
        transcript_empty=empty,
        verify=verify,
        number_checks=numbers or [],
        similar=similar,
        failures=failures or [],
        reading_status="ok",
    )


# ── 결론 ────────────────────────────────────────────────────


def test_l3_institution_plus_demand_is_suspected():
    r = _decide(_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST))
    assert r.scenario == Scenario.VOICE_PHISHING
    assert r.conclusion == Conclusion.SUSPECTED
    assert r.floors == ["L-3"]
    assert r.caution is False


@pytest.mark.parametrize(
    "demand",
    [
        E.MONEY_REQUEST,
        E.APP_INSTALL_REQUEST,
        E.REMOTE_APP_MENTION,
        E.INFO_REQUEST,
        E.AUTH_CODE_REQUEST,
        E.CASH_DELIVERY_REQUEST,
    ],
)
def test_l3_every_listed_demand(demand):
    r = _decide(_verify(E.INSTITUTION_CLAIM, demand))
    assert r.floors == ["L-3"]


def test_gift_card_alone_with_institution_is_not_l3():
    # gift_card 는 counts_for_floor 이지만 L-3 목록에는 없다
    r = _decide(_verify(E.INSTITUTION_CLAIM, E.GIFT_CARD_REQUEST))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.floors == []
    assert r.caution is True


def test_l3_acquaintance_plus_money_is_suspected():
    r = _decide(_verify(E.ACQUAINTANCE_CLAIM, E.MONEY_REQUEST))
    assert r.conclusion == Conclusion.SUSPECTED
    assert r.floors == ["L-3"]


def test_acquaintance_with_info_is_not_floor():
    r = _decide(_verify(E.ACQUAINTANCE_CLAIM, E.INFO_REQUEST))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is True


def test_demand_without_claim_is_inconclusive_caution():
    r = _decide(_verify(E.MONEY_REQUEST))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is True


def test_u4_split_failed_blocks_l3():
    r = _decide(_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST, split_failed=True))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is True
    assert r.floors == []


def test_u5_nothing_accepted_is_input_required():
    r = _decide(_verify())
    assert r.conclusion == Conclusion.INPUT_REQUIRED
    assert r.stop_actions == []
    assert r.questions == []


def test_u5_ignores_contact_blocker_only():
    r = _decide(_verify(E.CONTACT_BLOCKER))
    assert r.conclusion == Conclusion.INPUT_REQUIRED


def test_u5_spoken_host_counts_as_demand():
    r = _decide(_verify(spoken_hosts=["ykics24.kr"]))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is True
    assert r.stop_actions == [S.DONT_CLICK_LINK, S.HANG_UP]
    assert Unchecked(
        item=UncheckedItem.LINK_SPELLING, reason=UncheckedReason.READ_FROM_VOICE
    ) in r.unchecked


def test_u6_empty_transcript_is_not_analyzable():
    r = _decide(None, empty=True, failures=[FailureCode.EMPTY_INPUT])
    assert r.conclusion == Conclusion.NOT_ANALYZABLE
    assert r.stop_actions == [] and r.questions == []
    assert r.failures == [FailureCode.EMPTY_INPUT]
    assert CheckItem.TRANSCRIPT not in r.checked
    assert any(
        u.item == UncheckedItem.TRANSCRIPT and u.reason == UncheckedReason.FAILED
        for u in r.unchecked
    )


def test_u6_verify_none_even_if_transcript_present():
    assert _decide(None).conclusion == Conclusion.NOT_ANALYZABLE


def test_institution_alone_is_caution():
    r = _decide(_verify(E.INSTITUTION_CLAIM))
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution is True


def test_acquaintance_with_messenger_move_or_blocker_is_caution():
    for extra in (E.MESSENGER_MOVE, E.CONTACT_BLOCKER):
        r = _decide(_verify(E.ACQUAINTANCE_CLAIM, extra))
        assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution is True


def test_acquaintance_alone_is_inconclusive_without_caution():
    r = _decide(_verify(E.ACQUAINTANCE_CLAIM))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is False
    assert r.questions == []


def test_messenger_move_alone_is_inconclusive_without_caution():
    r = _decide(_verify(E.MESSENGER_MOVE))
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is False
    assert r.questions == []


def test_unofficial_number_is_caution_not_floor():
    nc = NumberCheck(number="0212345678", org="금융감독원", result="not_official")
    r = _decide(_verify(E.ACQUAINTANCE_CLAIM), numbers=[nc])
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert r.caution is True
    assert r.floors == []
    unofficial = [e for e in r.evidence if e.code == E.UNOFFICIAL_NUMBER]
    assert len(unofficial) == 1 and unofficial[0].counts_for_floor is False
    assert CheckItem.PHONE_NUMBER in r.checked


def test_official_number_adds_no_evidence():
    nc = NumberCheck(number="1332", org="금융감독원", result="official")
    r = _decide(_verify(E.INSTITUTION_CLAIM), numbers=[nc])
    assert all(e.code != E.UNOFFICIAL_NUMBER for e in r.evidence)


def test_reading_status_passthrough():
    r = decide_call(
        transcript_empty=False,
        verify=_verify(E.MONEY_REQUEST),
        number_checks=[],
        similar=None,
        failures=[],
        reading_status="fallback",
    )
    assert r.reading_status == "fallback"
    assert r.conclusion == Conclusion.INCONCLUSIVE


# ── 멈출 행동 ───────────────────────────────────────────────


def test_stop_actions_order_and_cap():
    r = _decide(
        _verify(
            E.INSTITUTION_CLAIM,
            E.CALL_REQUEST,
            E.AUTH_CODE_REQUEST,
            E.GIFT_CARD_REQUEST,
            E.MONEY_REQUEST,
            E.APP_INSTALL_REQUEST,
            E.INFO_REQUEST,
        )
    )
    assert r.stop_actions == [S.DONT_ENTER_INFO, S.DONT_INSTALL_APP, S.HANG_UP]


def test_stop_actions_hang_up_when_room():
    r = _decide(_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST))
    assert r.stop_actions == [S.DONT_SEND_MONEY, S.HANG_UP]


def test_stop_actions_no_dont_click_link_without_spoken_link():
    r = _decide(_verify(E.INSTITUTION_CLAIM))
    assert r.stop_actions == [S.HANG_UP]
    assert all(u.item != UncheckedItem.LINK_SPELLING for u in r.unchecked)


def test_n11_split_failed_keeps_hang_up_and_q1():
    r = _decide(
        _verify(
            E.INSTITUTION_CLAIM, E.MONEY_REQUEST, E.CALL_REQUEST, split_failed=True
        )
    )
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution is True
    assert r.floors == []
    assert [q.question_id.value for q in r.questions] == ["already_acted"]
    assert r.stop_actions == [
        S.DONT_SEND_MONEY,
        S.DONT_CALL_NUMBER_IN_MESSAGE,
        S.HANG_UP,
    ]


def test_split_failed_nothing_accepted_is_input_required():
    r = _decide(_verify(split_failed=True))
    assert r.conclusion == Conclusion.INPUT_REQUIRED


def test_no_caution_inconclusive_stop_is_hang_up_only():
    r = _decide(_verify(E.ACQUAINTANCE_CLAIM))
    assert r.caution is False
    assert r.stop_actions == [S.HANG_UP]
    assert r.questions == []


def test_n14_lexicon_derived_is_suspected():
    v = _verify(E.INSTITUTION_CLAIM, E.APP_INSTALL_REQUEST)
    v = v.model_copy(
        update={
            "accepted": [e.model_copy(update={"derived_by": "lexicon"}) for e in v.accepted]
        }
    )
    r = decide_call(
        transcript_empty=False,
        verify=v,
        number_checks=[],
        similar=None,
        failures=[],
        reading_status="fallback",
    )
    assert r.conclusion == Conclusion.SUSPECTED and r.floors == ["L-3"]


def test_phone_number_checked_only_when_compared():
    listed = NumberCheck(number="1", org="X", result="org_not_listed")
    r = _decide(_verify(E.MONEY_REQUEST), numbers=[listed])
    assert CheckItem.PHONE_NUMBER not in r.checked
    assert UncheckedItem.PHONE_NUMBER in {u.item for u in r.unchecked}
    off = NumberCheck(number="2", org="X", result="official")
    r = _decide(_verify(E.MONEY_REQUEST), numbers=[listed, off])
    assert CheckItem.PHONE_NUMBER in r.checked


def test_verify_none_transcript_not_checked():
    r = _decide(None)
    assert CheckItem.TRANSCRIPT not in r.checked
    assert (UncheckedItem.TRANSCRIPT, UncheckedReason.NOT_RUN) in {
        (u.item, u.reason) for u in r.unchecked
    }


# ── Q1 ─────────────────────────────────────────────────────


def test_q1_on_suspected_and_caution_without_clicked_link():
    for v in (_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST), _verify(E.INFO_REQUEST)):
        r = _decide(v)
        assert len(r.questions) == 1
        q = r.questions[0]
        assert q.question_id.value == "already_acted"
        assert q.option_ids == ["entered_info", "installed_app", "sent_money", "not_yet"]


# ── checked / unchecked / 유사 사례 ──────────────────────────


def test_checked_unchecked_defaults():
    r = _decide(_verify(E.MONEY_REQUEST))
    assert CheckItem.TRANSCRIPT in r.checked
    pairs = {(u.item, u.reason) for u in r.unchecked}
    assert (UncheckedItem.ACTUAL_SENDER, UncheckedReason.CANNOT_VERIFY) in pairs
    assert (UncheckedItem.TRANSCRIPT_ACCURACY, UncheckedReason.READ_FROM_VOICE) in pairs


def _similar(status, n=1):
    return CaseSearchResult(
        status=status,
        matches=[
            CaseMatch(case_id=f"v{i}", similarity=0.9, matched_variant="통째 대본 " * 30)
            for i in range(n)
        ],
    )


def test_similar_completed_is_checked_and_slimmed():
    r = _decide(_verify(E.MONEY_REQUEST), similar=_similar(AnalysisStatus.COMPLETED))
    assert CheckItem.SIMILAR_CASES in r.checked
    assert r.similar_cases is not None
    assert r.similar_cases.matches[0].case_id == "v0"
    assert r.similar_cases.matches[0].similarity == 0.9
    assert r.similar_cases.matches[0].matched_variant == ""


def test_similar_fallback_is_kb_empty():
    r = _decide(
        _verify(E.MONEY_REQUEST),
        similar=_similar(AnalysisStatus.FALLBACK, 0),
    )
    assert CheckItem.SIMILAR_CASES not in r.checked
    assert (UncheckedItem.SIMILAR_CASES, UncheckedReason.KB_EMPTY) in {
        (u.item, u.reason) for u in r.unchecked
    }


# ── 피해 대응 ───────────────────────────────────────────────


def test_response_sent_money_variants():
    assert response_actions_for(["sent_money"], [_ev(E.MONEY_REQUEST)]) == [
        R.REQUEST_PAYMENT_STOP,
        R.REPORT_112,
    ]
    assert response_actions_for(["sent_money"], [_ev(E.GIFT_CARD_REQUEST)]) == [
        R.CONTACT_GIFT_CARD_ISSUER,
        R.REPORT_112,
    ]


def test_response_entered_info_variants():
    assert response_actions_for(
        ["entered_info"], [_ev(E.INFO_REQUEST, "주민번호 불러 주세요")]
    ) == [R.REGISTER_INFO_EXPOSURE]
    assert response_actions_for(["entered_info"], [_ev(E.INFO_REQUEST, "계좌 알려줘")]) == [
        R.CHANGE_PASSWORD,
        R.REGISTER_INFO_EXPOSURE,
    ]
    assert response_actions_for(["entered_info"], []) == [
        R.CHANGE_PASSWORD,
        R.REGISTER_INFO_EXPOSURE,
    ]


def test_response_installed_app():
    # FE D17: 지급정지를 맨 앞에, 이어서 전원 끄기 → 다른 기기 → 앱 삭제
    assert response_actions_for(["installed_app"], []) == [
        R.REQUEST_PAYMENT_STOP,
        R.ISOLATE_PHONE,
        R.USE_OTHER_DEVICE,
        R.REMOVE_APP_AND_CHECK_DEVICE,
    ]


def test_response_never_uses_1332():
    assert "call_1332" not in {a.value for a in R}


def test_response_merge_follows_table_order_and_dedups():
    out = response_actions_for(
        ["installed_app", "entered_info", "sent_money"], [_ev(E.MONEY_REQUEST)]
    )
    assert out == [
        R.REQUEST_PAYMENT_STOP,
        R.REPORT_112,
        R.CHANGE_PASSWORD,
        R.REGISTER_INFO_EXPOSURE,
        R.ISOLATE_PHONE,
        R.USE_OTHER_DEVICE,
        R.REMOVE_APP_AND_CHECK_DEVICE,
    ]


def test_response_not_yet_adds_nothing_and_keeps_earlier():
    assert response_actions_for(["not_yet", "unknown"], []) == []
    assert response_actions_for(["sent_money", "not_yet"], []) == [
        R.REQUEST_PAYMENT_STOP,
        R.REPORT_112,
    ]


def test_default_response_actions():
    assert default_response_actions("sent_money") == [
        R.REQUEST_PAYMENT_STOP,
        R.CONTACT_GIFT_CARD_ISSUER,
        R.REPORT_112,
    ]
    assert default_response_actions("entered_info") == [
        R.CHANGE_PASSWORD,
        R.REGISTER_INFO_EXPOSURE,
    ]
    assert default_response_actions("installed_app") == [
        R.REQUEST_PAYMENT_STOP,
        R.ISOLATE_PHONE,
        R.USE_OTHER_DEVICE,
        R.REMOVE_APP_AND_CHECK_DEVICE,
    ]
    assert default_response_actions("not_yet") == []


def test_apply_call_answers_fills_response_actions_only():
    result = decide_call(
        transcript_empty=False,
        verify=_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST),
        number_checks=[],
        similar=None,
        failures=[],
    )
    assert result.response_actions == []

    answered = apply_call_answers(result, ["sent_money", "not_yet"])

    assert answered.response_actions == [R.REQUEST_PAYMENT_STOP, R.REPORT_112]
    # 결론·주의·멈출 행동·질문은 답으로 바뀌지 않는다 (v3 §8)
    assert answered.model_dump(exclude={"response_actions"}) == result.model_dump(
        exclude={"response_actions"}
    )
    assert result.response_actions == []  # 원본은 그대로


def test_apply_call_answers_ignores_unknown_options():
    result = decide_call(
        transcript_empty=False,
        verify=_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST),
        number_checks=[],
        similar=None,
        failures=[],
    )
    assert apply_call_answers(result, ["clicked_link", "unknown"]).response_actions == []


# ── 설명 ───────────────────────────────────────────────────

FORBIDDEN = ("안전합니다", "안전한", "정상입니다", "정상 문자", "문제없", "걱정 마")


def test_templates_have_no_forbidden_words():
    texts = [*TEMPLATES.values(), *LEADS.values()]
    for t in texts:
        assert not any(w in t for w in FORBIDDEN), t


def test_templates_cover_voice_codes():
    for code in (
        E.INSTITUTION_CLAIM,
        E.ACQUAINTANCE_CLAIM,
        E.CONTACT_BLOCKER,
        E.UNOFFICIAL_NUMBER,
        E.MONEY_REQUEST,
        E.CASH_DELIVERY_REQUEST,
        E.GIFT_CARD_REQUEST,
        E.AUTH_CODE_REQUEST,
        E.INFO_REQUEST,
        E.APP_INSTALL_REQUEST,
        E.REMOTE_APP_MENTION,
        E.CALL_REQUEST,
        E.MESSENGER_MOVE,
    ):
        assert code in TEMPLATES


def test_explanation_built_from_templates():
    r = _decide(_verify(E.INSTITUTION_CLAIM, E.MONEY_REQUEST))
    assert TEMPLATES[E.MONEY_REQUEST] in r.explanation
    assert r.explanation.startswith(LEADS[Conclusion.SUSPECTED])
    assert explain_call(Conclusion.NOT_ANALYZABLE, []) == LEADS[Conclusion.NOT_ANALYZABLE]
