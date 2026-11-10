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


@pytest.mark.parametrize(
    "text, name, number",
    [
        ("[국세청] 고객님 앞으로 미납된 종합소득세가 있습니다", "국세청", "126"),
        ("[건강보험공단] 고객님 앞으로 미납된 보험료가 있습니다", "건강보험공단", "15771000"),
    ],
)
async def test_public_institution_text_is_caution_with_official_number(text, name, number):
    """요구가 없어도 "확인할 주소가 없어요" 로 끝내지 않고 공식 번호로 확인하게 한다."""
    reading = {"identity": [{"kind": "institution", "name": name, "quote": text[: text.index("]") + 1]}]}
    r = await _analyze(text, reading)
    assert r.scenario == Scenario.IMPERSONATION_NO_LINK
    assert (r.conclusion, r.caution, r.floors) == (Conclusion.INCONCLUSIVE, True, [])
    assert r.official_contact is not None and r.official_contact.number == number


async def test_public_institution_uses_lexicon_when_reading_fails():
    source = message_source("[국세청] 고객님 앞으로 미납된 종합소득세가 있습니다")
    reading = read_with_lexicon(source, FailureCode.TIMEOUT).reading
    assert [(i.kind.value, i.name) for i in reading.identity] == [("institution", "국세청")]


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
    assert CheckItem.MESSAGE_TEXT not in r.checked


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


async def test_answer_on_not_analyzable_keeps_conclusion_but_gives_response():
    r = await analyze_message_case("가" * 9000, runtime=_runtime(None))
    r2 = apply_message_answer(r, "already_acted", "sent_money")
    assert r2.conclusion == Conclusion.NOT_ANALYZABLE
    assert r2.answers.already_acted == ["sent_money"]
    assert r2.response_actions == [ResponseAction.REQUEST_PAYMENT_STOP, ResponseAction.REPORT_112]


@pytest.mark.parametrize("settled", ["not_person", "is_person"])
@pytest.mark.parametrize("later", ["not_person", "is_person", "no_answer", "not_yet", "unknown"])
async def test_settled_q2_ignores_later_q2_answers(settled, later):
    """대화창에 남은 예전 Q2 카드를 다시 눌러도 정해진 결론이 바뀌지 않는다."""
    first = apply_message_answer(await _analyze(F1, F1_READING), "called_known_number", settled)
    assert apply_message_answer(first, "called_known_number", later) == first


async def test_not_person_without_l4_still_marks_answer_basis():
    reading = {
        "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나야"}],
        "demands": [{"kind": "move_messenger", "quote": "이 번호로 문자해"}],
    }
    r = await _analyze("엄마 나야\n이 번호로 문자해", reading)
    assert "called_known_number" in _q(r)
    r = apply_message_answer(r, "called_known_number", "not_person")
    assert r.conclusion == Conclusion.INCONCLUSIVE and r.floors == []
    assert r.basis_from_answer is True
    assert CheckItem.USER_ANSWER in r.checked


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


# ── PR #147 리뷰: 부탁 꼴·공공기관 자기소개가 근거에서 빠지면 안 된다 ──────────


FAVOR = "엄마 나야. 50만원만 보내줄래요?"
FAVOR_READING = {
    "identity": [{"kind": "acquaintance", "name": "자녀", "quote": "엄마 나야"}],
    "demands": [{"kind": "money_transfer", "quote": "50만원만 보내줄래요"}],
}


@pytest.mark.parametrize("reading", [FAVOR_READING, None], ids=["llm", "lexicon"])
async def test_julraeyo_favor_gets_caution_and_questions(reading):
    r = await _analyze(FAVOR, reading)
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, True)
    assert list(_q(r)) == ["already_acted", "called_known_number"]


@pytest.mark.parametrize("reading", [None], ids=["lexicon"])
async def test_echoed_demand_gets_no_caution(reading):
    r = await _analyze("엄마 나야. 50만원만 보내라고요?", reading)
    assert r.caution is False
    assert _q(r) == {}


def _public_reading(quote):
    return {"identity": [{"kind": "institution", "name": "국세청", "quote": quote}]}


@pytest.mark.parametrize(
    "text, reading",
    [
        ("저는 국세청 직원입니다", _public_reading("저는 국세청 직원입니다")),
        ("국세청 직원입니다", _public_reading("국세청 직원입니다")),
        ("저는 국세청 직원입니다", None),
        ("국세청 직원입니다", None),
    ],
    ids=["first-person-llm", "bare-llm", "first-person-lexicon", "bare-lexicon"],
)
async def test_public_institution_self_intro_gets_same_caution(text, reading):
    """"저는" 유무와 읽기 방식이 달라도 같은 주의 안내와 공식 번호로 이어진다."""
    r = await _analyze(text, reading)
    assert (r.conclusion, r.caution, r.floors) == (Conclusion.INCONCLUSIVE, True, [])
    assert list(_q(r)) == ["already_acted"]
    assert r.official_contact is not None and r.official_contact.number == "126"
    assert r.claims.identity[0].name == "국세청"


# 합성 사례(2026-11-05 검수). LLM 이 정확히 읽은 경우와 어휘표 폴백을 모두 본다
_ACQ = {"kind": "acquaintance", "name": "자녀", "quote": "엄마 나야"}


@pytest.mark.parametrize(
    "sentence, kind",
    [
        ("돈 좀 보낼 수 있어?", "money_transfer"),
        ("급한데 돈 좀 보내면 안 될까?", "money_transfer"),
        ("오늘까지 50만원만 입금 가능해요?", "money_transfer"),
        ("송금 좀 해 주겠어요?", "money_transfer"),
        ("돈 좀 보낼래요?", "money_transfer"),
        ("50만원만 빌려줄래요?", "money_transfer"),
        ("상품권 좀 사 볼래요?", "gift_card"),
        ("인증번호 나한테 보낼 수 있어?", "auth_code"),
        ("이 앱 설치해 볼래요?", "app_install"),
        ("신분증 사진 좀 보낼래요?", "personal_info"),
    ],
)
@pytest.mark.parametrize("path", ["llm", "lexicon"])
async def test_asking_sentence_gets_caution(sentence, kind, path):
    reading = {"identity": [_ACQ], "demands": [{"kind": kind, "quote": sentence.rstrip("?")}]}
    r = await _analyze(f"엄마 나야. {sentence}", reading if path == "llm" else None)
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, True)


@pytest.mark.parametrize(
    "sentence, name",
    [
        ("저는 국세청에서 나온 직원입니다", "국세청"),
        ("저는 경찰청에서 근무하는 김민수 경위입니다", "경찰청"),
        ("저는 국세청 직원이거든요", "국세청"),
        ("저는 국세청 직원이며 미납 건으로 문자드립니다", "국세청"),
        ("저는 김민수 검사입니다", "검사"),
        ("나는 국세청 직원이야", "국세청"),
    ],
)
@pytest.mark.parametrize("path", ["llm", "lexicon"])
async def test_self_intro_sentence_gets_caution(sentence, name, path):
    reading = {"identity": [{"kind": "institution", "name": name, "quote": sentence}]}
    r = await _analyze(sentence, reading if path == "llm" else None)
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, True)


@pytest.mark.parametrize(
    "sentence, reading",
    [
        (
            "저는 국세청 직원이 아니라 민원인입니다",
            {"identity": [{"kind": "institution", "name": "국세청", "quote": "저는 국세청 직원이 아니라 민원인입니다"}]},
        ),
        (
            "제가 경찰에 신고한 사람입니다",
            {"identity": [{"kind": "institution", "name": "경찰", "quote": "제가 경찰에 신고한 사람입니다"}]},
        ),
        (
            "저는 건강보험공단 가입자입니다",
            {"identity": [{"kind": "institution", "name": "건강보험공단", "quote": "저는 건강보험공단 가입자입니다"}]},
        ),
        (
            "제가 대신 입금해 줄까요?",
            {"demands": [{"kind": "money_transfer", "quote": "제가 대신 입금해 줄까요"}]},
        ),
    ],
)
@pytest.mark.parametrize("path", ["llm", "lexicon"])
async def test_sentence_not_from_impersonator_gets_no_caution(sentence, reading, path):
    r = await _analyze(sentence, reading if path == "llm" else None)
    assert r.caution is False
    assert _q(r) == {}


# ── #160: 모르는 사람이 메신저로 옮기라는 문자 ─────────────────

STRANGER_MESSENGER = (
    "퇴근 후에 좀 쉬고 싶으신가요?\n고르실 사진 보내드릴테니 제 라인 추가해 주세요 : zxf226"
)
STRANGER_MESSENGER_READING = {
    "demands": [{"kind": "move_messenger", "quote": "제 라인 추가해 주세요"}],
}


async def test_stranger_messenger_move_is_caution_without_q2():
    r = await _analyze(STRANGER_MESSENGER, STRANGER_MESSENGER_READING)
    assert (r.conclusion, r.caution, r.floors) == (Conclusion.INCONCLUSIVE, True, [])
    # Q2(원래 번호로 통화)는 지인 주장에만 붙는다
    assert list(_q(r)) == ["already_acted"]


# 가게 광고("채널 추가")는 개인 아이디가 없으니 주의로 올리지 않는다 (#160 수정의 오탐 방지)
SHOP_AD = "카카오톡 채널 추가하시면 할인 쿠폰을 드려요"


async def test_shop_channel_ad_is_not_caution():
    r = await _analyze(SHOP_AD, {"demands": [{"kind": "move_messenger", "quote": "카카오톡 채널 추가하시면"}]})
    assert r.caution is False


@pytest.mark.parametrize("text", [
    "[국제발신]\nんな時間に連絡とか、変なタイミングでごめん。…でも今しかなかった。Line : mx5782",
    '[국제발신]\n은행 여직원 모시겠어요? 추가 후 답장"1" LINE : ck5529',
    "고수익 부업 안내. 텔레그램 @abc12 로 문의 주세요",
    "카톡 아이디 happyday 로 친구 추가해 주세요",
])
async def test_messenger_handle_spam_is_caution_by_lexicon(text):
    # LLM 이 실패해도(어휘표) 메신저 아이디로 옮기라는 문자는 주의다
    r = await _analyze(text, None)
    assert (r.conclusion, r.caution) == (Conclusion.INCONCLUSIVE, True)


async def test_stranger_messenger_caution_survives_q1_answer():
    # LLM 인용("제 라인 추가해 주세요")에 아이디가 없어도 같은 줄의 아이디를 근거로 남겨 답 뒤에도 주의다
    r = await _analyze(STRANGER_MESSENGER, STRANGER_MESSENGER_READING)
    after = apply_message_answer(r, "already_acted", "not_yet")
    assert (after.conclusion, after.caution) == (Conclusion.INCONCLUSIVE, True)


@pytest.mark.parametrize("text", [
    "내일 회의는 줌으로 진행합니다. 회의 ID: 123 456 7890",
    "주문하신 상품 영수증 번호 1234-5678-9012-3456 입니다",
])
async def test_masked_number_alone_is_not_info_request(text):
    # 가림 표시(<계좌번호>·<카드번호>)의 글자를 개인정보 요구로 읽지 않는다
    r = await _analyze(text, None)
    assert r.caution is False
    assert all(e.code != E.INFO_REQUEST for e in r.evidence)


async def test_real_account_request_with_masked_number_is_still_caution():
    r = await _analyze("계좌번호 123-456-789012 로 지금 입금해 주세요", None)
    assert r.caution is True
