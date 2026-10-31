"""analyze_message_case·apply_message_answer: 링크 없는 문자 시나리오 (v3 §7·§8·§9 F1·M2·N1·N2).

가짜 런타임만 쓴다. 실제 LLM·임베딩은 부르지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from ai.case import MessageRuntime, analyze_message_case, apply_message_answer
from ai.case.explain import MESSAGE_LEADS, MESSAGE_TEMPLATES
from ai.case.lexicon import read_with_lexicon
from ai.case.message import message_source
from ai.case.types import (
    CheckItem,
    ClaimReading,
    ClaimReadingResult,
    Conclusion,
    EvidenceCode,
    InputKind,
    Origin,
    ResponseAction,
    Scenario,
    StopAction,
    UncheckedItem,
    UncheckedReason,
    VerifyAction,
)
from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult, FailureCode

E = EvidenceCode
_EMPTY = CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[])

F1 = "엄마 나 폰 고장나서 이 번호로 문자해. 문화상품권 사서 핀번호 보내줘"
F1_READING = {
    "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나 폰 고장나서"}],
    "blockers": [{"quote": "폰 고장나서"}],
    "demands": [
        {"kind": "move_messenger", "quote": "이 번호로 문자해"},
        {"kind": "gift_card", "quote": "문화상품권 사서 핀번호 보내줘"},
    ],
}
M2 = "엄마 나야\n휴대폰이 고장 났어\n통화는 안 돼\n급하게 결제할 게 있는데 이 계좌로 대신 보내줘\n국민 123456789012"
M2_READING = {
    "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나야"}],
    "blockers": [{"quote": "휴대폰이 고장 났어"}, {"quote": "통화는 안 돼"}],
    "pretexts": [{"quote": "급하게 결제할 게 있는데"}],
    "demands": [{"kind": "money_transfer", "quote": "이 계좌로 대신 보내줘"}],
}
INSTITUTION = (
    "[Web발신] 서울중앙지검 수사관입니다. 고객님 명의 계좌가 범죄에 연루되었습니다. "
    "확인을 위해 안전계좌로 이체하셔야 합니다"
)
INSTITUTION_READING = {
    "identity": [
        {"kind": "institution", "name": "서울중앙지검", "quote": "서울중앙지검 수사관입니다"}
    ],
    "pretexts": [{"quote": "고객님 명의 계좌가 범죄에 연루되었습니다"}],
    "demands": [{"kind": "money_transfer", "quote": "안전계좌로 이체하셔야 합니다"}],
}


class Calls:
    def __init__(self) -> None:
        self.reading = 0
        self.search_queries: list[str] = []
        self.sources = []


def _runtime(reading: dict | None, calls: Calls | None = None, *, search=_EMPTY) -> MessageRuntime:
    calls = calls or Calls()

    async def fake_read(source):
        calls.reading += 1
        calls.sources.append(source)
        if reading is None:
            return read_with_lexicon(source, FailureCode.TIMEOUT)
        return ClaimReadingResult(status="ok", reading=ClaimReading.model_validate(reading))

    def fake_search(text):
        calls.search_queries.append(text)
        if isinstance(search, Exception):
            raise search
        return search

    return MessageRuntime(read_claims=fake_read, search_cases=fake_search)


async def _analyze(text, reading, **kwargs):
    return await analyze_message_case(text, runtime=_runtime(reading, **kwargs))


def _q(result):
    return {q.question_id.value: q.option_ids for q in result.questions}


# ── F1: 가족 사칭 + 상품권 ───────────────────────────────────


async def test_f1_is_caution_with_q1_and_q2():
    r = await _analyze(F1, F1_READING)
    assert r.scenario == Scenario.IMPERSONATION_NO_LINK
    assert (r.conclusion, r.caution, r.floors) == (Conclusion.INCONCLUSIVE, True, [])
    assert r.stop_actions == [StopAction.DONT_SEND_GIFT_CARD]
    assert list(_q(r)) == ["already_acted", "called_known_number"]
    assert _q(r)["already_acted"] == ["entered_info", "installed_app", "sent_money", "not_yet"]
    assert _q(r)["called_known_number"] == ["not_person", "is_person", "no_answer", "not_yet", "unknown"]
    assert r.verify_actions == [VerifyAction.CALL_KNOWN_NUMBER]
    assert r.basis_from_answer is False
    assert all(e.origin == Origin.MESSAGE for e in r.evidence)
    assert r.claims.identity[0].quote == "엄마 나 폰 고장나서"


async def test_f1_q2_not_person_is_suspected_l4():
    r = apply_message_answer(await _analyze(F1, F1_READING), "called_known_number", "not_person")
    assert (r.conclusion, r.floors) == (Conclusion.SUSPECTED, ["L-4"])
    assert r.basis_from_answer is True
    assert list(_q(r)) == ["already_acted"]
    assert r.verify_actions == []
    assert CheckItem.USER_ANSWER in r.checked
    assert any(e.code == E.USER_NOT_PERSON and e.origin == Origin.USER_ANSWER for e in r.evidence)
    assert r.explanation.startswith(MESSAGE_LEADS[Conclusion.SUSPECTED])
    assert "스미싱" not in r.explanation


async def test_f1_q2_not_yet_keeps_result():
    before = await _analyze(F1, F1_READING)
    r = apply_message_answer(before, "called_known_number", "not_yet")
    assert (r.conclusion, r.caution) == (before.conclusion, before.caution)
    assert list(_q(r)) == ["already_acted", "called_known_number"]
    assert r.verify_actions == [VerifyAction.CALL_KNOWN_NUMBER]
    assert r.answers.called_known_number == "not_yet"
    assert CheckItem.USER_ANSWER not in r.checked


@pytest.mark.parametrize("option", ["no_answer", "unknown"])
async def test_f1_q2_unresolved_answers_keep_asking(option):
    r = apply_message_answer(await _analyze(F1, F1_READING), "called_known_number", option)
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution
    assert "called_known_number" in _q(r)


async def test_f1_q2_is_person_turns_caution_off_only():
    r = apply_message_answer(await _analyze(F1, F1_READING), "called_known_number", "is_person")
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, False)
    assert r.verify_actions == [VerifyAction.CHECK_ACCOUNT_HOLDER]
    assert r.questions == []
    assert r.basis_from_answer is True
    # 멈출 행동은 그대로 남는다. 결론을 '안전' 쪽으로 옮기지 않는다
    assert r.stop_actions == [StopAction.DONT_SEND_GIFT_CARD]


async def test_f1_q1_sent_money_then_q2_not_person():
    r = apply_message_answer(await _analyze(F1, F1_READING), "already_acted", "sent_money")
    assert r.response_actions == [ResponseAction.CONTACT_GIFT_CARD_ISSUER, ResponseAction.REPORT_112]
    assert r.conclusion == Conclusion.INCONCLUSIVE
    assert "called_known_number" in _q(r)
    r = apply_message_answer(r, "called_known_number", "not_person")
    assert r.conclusion == Conclusion.SUSPECTED
    # 앞에서 고른 대응은 남는다
    assert r.response_actions == [ResponseAction.CONTACT_GIFT_CARD_ISSUER, ResponseAction.REPORT_112]


async def test_q1_answers_accumulate_and_not_yet_does_not_clear():
    r = await _analyze(M2, M2_READING)
    r = apply_message_answer(r, "already_acted", "entered_info")
    r = apply_message_answer(r, "already_acted", "sent_money")
    r = apply_message_answer(r, "already_acted", "not_yet")
    assert r.answers.already_acted == ["entered_info", "sent_money", "not_yet"]
    assert r.response_actions == [
        ResponseAction.REQUEST_PAYMENT_STOP,
        ResponseAction.REPORT_112,
        ResponseAction.CHANGE_PASSWORD,
        ResponseAction.REGISTER_INFO_EXPOSURE,
    ]


async def test_installed_app_in_message_has_no_payment_stop():
    r = apply_message_answer(await _analyze(M2, M2_READING), "already_acted", "installed_app")
    assert r.response_actions == [
        ResponseAction.ISOLATE_PHONE,
        ResponseAction.USE_OTHER_DEVICE,
        ResponseAction.REMOVE_APP_AND_CHECK_DEVICE,
    ]


# ── M2: 흩어진 주장 ─────────────────────────────────────────


async def test_m2_masks_account_and_binds_scattered_claims():
    calls = Calls()
    r = await analyze_message_case(M2, runtime=_runtime(M2_READING, calls))
    assert "123456789012" not in "\n".join(s.text for s in calls.sources[0].segments)
    assert "<계좌번호>" in calls.search_queries[0]
    assert calls.sources[0].kind == InputKind.TEXT
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, True)
    assert r.stop_actions == [StopAction.DONT_SEND_MONEY]
    assert r.claims.blockers == ["휴대폰이 고장 났어", "통화는 안 돼"]
    assert list(_q(r)) == ["already_acted", "called_known_number"]
    r = apply_message_answer(r, "called_known_number", "not_person")
    assert (r.conclusion, r.floors) == (Conclusion.SUSPECTED, ["L-4"])


async def test_lexicon_fallback_still_reads_family_message():
    r = await _analyze(M2, None)
    assert r.reading_status == "fallback"
    assert FailureCode.TIMEOUT in r.failures
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution
    assert StopAction.DONT_SEND_MONEY in r.stop_actions
    assert "called_known_number" in _q(r)


async def test_acquaintance_without_money_demand_is_not_l4():
    reading = {
        "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나야"}],
        "demands": [{"kind": "personal_info", "quote": "신분증 사진 찍어서 보내줘"}],
    }
    r = await _analyze("엄마 나야\n신분증 사진 찍어서 보내줘", reading)
    r = apply_message_answer(r, "called_known_number", "not_person")
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.floors == []
    assert r.caution


# ── 기관 사칭 문자 ──────────────────────────────────────────


async def test_institution_text_is_caution_without_l3():
    r = await _analyze(INSTITUTION, INSTITUTION_READING)
    assert r.scenario == Scenario.IMPERSONATION_NO_LINK
    assert (r.conclusion, r.caution, r.floors) == (Conclusion.INCONCLUSIVE, True, [])
    assert r.stop_actions == [StopAction.DONT_SEND_MONEY]
    assert list(_q(r)) == ["already_acted"]
    assert r.verify_actions == []
    assert r.claims.identity[0].listed_institution is True
    assert r.official_contact is not None and r.official_contact.number


async def test_q2_answer_on_institution_text_is_ignored_for_conclusion():
    r = apply_message_answer(await _analyze(INSTITUTION, INSTITUTION_READING), "called_known_number", "is_person")
    assert r.caution is True
    assert r.basis_from_answer is False


# ── 경계 ────────────────────────────────────────────────────


async def test_chatter_is_input_required():
    r = await _analyze("배고파", {})
    assert (r.scenario, r.conclusion) == (Scenario.UNKNOWN, Conclusion.INPUT_REQUIRED)
    assert r.stop_actions == [] and r.questions == []


async def test_n1_notice_sentence_is_dropped():
    reading = {
        "identity": [{"kind": "company", "name": "국민은행", "quote": "[국민은행] 10월 급여가"}],
        "demands": [{"kind": "money_transfer", "quote": "10월 급여가 입금되었습니다"}],
    }
    r = await _analyze("[국민은행] 10월 급여가 입금되었습니다", reading)
    assert r.conclusion == Conclusion.INPUT_REQUIRED
    assert any(x.reason == "notice_sentence" for x in r.rejected)


async def test_n2_negated_demand_is_dropped():
    reading = {"demands": [{"kind": "app_install", "quote": "앱 설치나 송금을 요구하지 않습니다"}]}
    r = await _analyze("[OO카드] 당사는 어떠한 경우에도 앱 설치나 송금을 요구하지 않습니다", reading)
    assert r.conclusion == Conclusion.INPUT_REQUIRED


async def test_quote_not_in_message_is_rejected():
    reading = {
        "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나 아들이야"}],
        "demands": [{"kind": "money_transfer", "quote": "50만원 보내줘"}],
    }
    r = await _analyze("오늘 저녁 뭐 먹을까", reading)
    assert r.conclusion == Conclusion.INPUT_REQUIRED
    assert all(x.reason == "not_in_source" and x.quote == "" for x in r.rejected)


async def test_empty_text_does_not_call_llm():
    calls = Calls()
    r = await analyze_message_case("   ", runtime=_runtime(F1_READING, calls))
    assert calls.reading == 0
    assert r.conclusion == Conclusion.INPUT_REQUIRED
    assert FailureCode.EMPTY_INPUT in r.failures


async def test_too_long_text_is_not_analyzable():
    calls = Calls()
    r = await analyze_message_case("가" * 9000, runtime=_runtime(F1_READING, calls))
    assert calls.reading == 0
    assert r.conclusion == Conclusion.NOT_ANALYZABLE


async def test_zero_budget_uses_lexicon_without_llm():
    calls = Calls()
    r = await analyze_message_case(M2, runtime=_runtime(M2_READING, calls), time_budget_s=0)
    assert calls.reading == 0 and calls.search_queries == []
    assert r.reading_status == "fallback"
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.caution
    assert any(
        u.item == UncheckedItem.SIMILAR_CASES and u.reason == UncheckedReason.TIMEOUT
        for u in r.unchecked
    )


async def test_reading_raises_falls_back_to_lexicon():
    async def broken(source):
        raise RuntimeError("boom")

    runtime = MessageRuntime(read_claims=broken, search_cases=lambda t: _EMPTY)
    r = await analyze_message_case(M2, runtime=runtime)
    assert r.reading_status == "fallback"
    assert FailureCode.LLM_ERROR in r.failures


async def test_slow_reading_times_out_to_lexicon(monkeypatch):
    import ai.case.message as message

    monkeypatch.setattr(message, "READ_BUDGET_S", 0.05)
    monkeypatch.setattr(message, "_READ_GRACE_S", 0.0)

    async def slow(source):
        await asyncio.sleep(5)

    runtime = MessageRuntime(read_claims=slow, search_cases=lambda t: _EMPTY)
    r = await analyze_message_case(M2, runtime=runtime)
    assert FailureCode.TIMEOUT in r.failures
    assert r.conclusion == Conclusion.INCONCLUSIVE


async def test_search_failure_is_unchecked_and_survives_answers():
    r = await _analyze(F1, F1_READING, search=RuntimeError("kb"))
    miss = [u for u in r.unchecked if u.item == UncheckedItem.SIMILAR_CASES]
    assert miss and miss[0].reason == UncheckedReason.FAILED
    r = apply_message_answer(r, "called_known_number", "not_person")
    miss = [u for u in r.unchecked if u.item == UncheckedItem.SIMILAR_CASES]
    assert miss and miss[0].reason == UncheckedReason.FAILED


async def test_similar_cases_found_is_checked_and_slimmed():
    found = CaseSearchResult(
        status=AnalysisStatus.COMPLETED,
        matches=[CaseMatch(case_id="CE-0430", similarity=0.5, matched_variant="본문", categories=[])],
    )
    r = await _analyze(F1, F1_READING, search=found)
    assert CheckItem.SIMILAR_CASES in r.checked
    assert r.similar_cases.matches[0].matched_variant == ""


async def test_actual_sender_is_always_unchecked():
    r = await _analyze(F1, F1_READING)
    assert any(
        u.item == UncheckedItem.ACTUAL_SENDER and u.reason == UncheckedReason.CANNOT_VERIFY
        for u in r.unchecked
    )


async def test_unknown_question_or_option_is_ignored():
    r = await _analyze(F1, F1_READING)
    assert apply_message_answer(r, "nope", "not_person") == r
    assert apply_message_answer(r, "called_known_number", "maybe") == r
    assert apply_message_answer(r, "already_acted", "clicked_link") == r


async def test_answer_on_not_analyzable_keeps_conclusion():
    r = await analyze_message_case("가" * 9000, runtime=_runtime(None))
    r2 = apply_message_answer(r, "already_acted", "sent_money")
    assert r2.conclusion == Conclusion.NOT_ANALYZABLE
    assert r2.answers.already_acted == ["sent_money"]


async def test_q1_on_input_required_still_gives_response():
    r = apply_message_answer(await _analyze("배고파", {}), "already_acted", "sent_money")
    assert r.conclusion == Conclusion.INPUT_REQUIRED
    assert r.response_actions == [ResponseAction.REQUEST_PAYMENT_STOP, ResponseAction.REPORT_112]


async def test_result_round_trips_through_json():
    r = apply_message_answer(await _analyze(F1, F1_READING), "already_acted", "sent_money")
    from ai.case.types import MessageCaseResult

    again = MessageCaseResult.model_validate(r.model_dump(mode="json"))
    assert again == r
    assert apply_message_answer(again, "called_known_number", "not_person").conclusion == Conclusion.SUSPECTED


async def test_unexpected_error_returns_not_analyzable(monkeypatch):
    import ai.case.message as message

    def boom(text):
        raise ValueError("x")

    monkeypatch.setattr(message, "message_source", boom)
    r = await analyze_message_case(F1, runtime=_runtime(F1_READING))
    assert r.conclusion == Conclusion.NOT_ANALYZABLE


def test_message_source_splits_lines_and_masks():
    src = message_source("엄마 나야\n\n주민번호 900101-1234567 보내줘\n")
    assert [s.text for s in src.segments] == ["엄마 나야", "주민번호 <주민번호> 보내줘"]


FORBIDDEN = ("안전합니다", "안전한", "정상입니다", "정상 문자", "문제없", "걱정 마", "스미싱")


def test_message_templates_have_no_forbidden_words():
    for text in [*MESSAGE_TEMPLATES.values(), *MESSAGE_LEADS.values()]:
        assert not any(w in text for w in FORBIDDEN), text
        assert "통화만으로" not in text
