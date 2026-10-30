"""analyze_call: 통화 분석 진입 함수와 사례 픽스처 (v3 §9). 가짜 런타임만 쓴다."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml

import ai.case.voice as voice
from ai.case import VoiceRuntime, analyze_call, warm_up_voice_case_search
from ai.case.lexicon import read_with_lexicon
from ai.case.types import (
    ClaimReading,
    ClaimReadingResult,
    Conclusion,
    Segment,
    Side,
    TranscriptResult,
    UncheckedItem,
    UncheckedReason,
)
from ai.kb.numbers import check_phone_number
from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult, FailureCode

CASES_DIR = Path(__file__).parent / "fixtures" / "cases" / "voice"
CASE_FILES = sorted(CASES_DIR.glob("*.yaml"))

_COMPLETED = CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[])
_KB_EMPTY = CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[])


class Calls:
    def __init__(self) -> None:
        self.reading = 0
        self.search = 0
        self.search_queries: list[str] = []
        self.numbers: list[tuple[str, str]] = []


def _transcript(case: dict) -> TranscriptResult:
    # 화자 표기 없이 받아쓴 글 그대로다 (ADR 0007)
    return TranscriptResult(
        segments=[Segment(side=Side.UNKNOWN, text=text) for text in case["transcript"]]
    )


def _runtime(case: dict, calls: Calls) -> VoiceRuntime:
    spec = case.get("runtime") or {}

    async def fake_read(source):
        calls.reading += 1
        if "reading" not in spec or spec["reading"] is None:
            # read_claims 가 시간 초과로 어휘표에 폴백한 결과와 같다
            return read_with_lexicon(source, FailureCode.TIMEOUT)
        return ClaimReadingResult(
            status="ok", reading=ClaimReading.model_validate(spec["reading"])
        )

    def fake_search(text):
        calls.search += 1
        calls.search_queries.append(text)
        return _KB_EMPTY if spec.get("search") == "kb_empty" else _COMPLETED

    def fake_number(number, org):
        calls.numbers.append((number, org))
        return check_phone_number(number, org)

    return VoiceRuntime(
        read_claims=fake_read, search_cases=fake_search, check_phone_number=fake_number
    )


@pytest.mark.parametrize("path", CASE_FILES, ids=lambda p: p.stem)
def test_case_fixture(path):
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    expect = case["expect"]
    calls = Calls()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    assert result.conclusion.value == expect["conclusion"]
    if "caution" in expect:
        assert result.caution is expect["caution"]
    if "floors" in expect:
        assert result.floors == expect["floors"]
    if "stop_actions" in expect:
        assert [a.value for a in result.stop_actions] == expect["stop_actions"]
    if "questions" in expect:
        assert {
            q.question_id.value: q.option_ids for q in result.questions
        } == expect["questions"]
    if "evidence_includes" in expect:
        codes = {e.code.value for e in result.evidence}
        assert set(expect["evidence_includes"]) <= codes
    if "number_checks" in expect:
        got = [
            {"number": c.number, "org": c.org, "result": c.result}
            for c in result.number_checks
        ]
        assert got == expect["number_checks"]
    if "unchecked_includes" in expect:
        got = {f"{u.item.value}:{u.reason.value}" for u in result.unchecked}
        assert set(expect["unchecked_includes"]) <= got
    if "failures" in expect:
        assert [f.value for f in result.failures] == expect["failures"]
    if "rejected" in expect:
        assert [r.reason for r in result.rejected] == expect["rejected"]
    if "derived_by" in expect:
        assert {
            e.derived_by for e in result.evidence if e.origin.value == "transcript"
        } == {expect["derived_by"]}
    if "reading_status" in expect:
        assert result.reading_status == expect["reading_status"]
    if "reading_calls" in expect:
        assert calls.reading == expect["reading_calls"]
    if "search_calls" in expect:
        assert calls.search == expect["search_calls"]


def _v1() -> dict:
    return yaml.safe_load((CASES_DIR / "V1.yaml").read_text(encoding="utf-8"))


def test_search_query_is_whole_masked_transcript():
    case = _v1()
    case["transcript"][0] = "여보세요 주민번호 900101-1234567"
    calls = Calls()

    asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    query = calls.search_queries[0]
    assert "<주민번호>" in query and "900101" not in query
    assert "여보세요" in query and "안전계좌" in query and "어떻게 해야 하죠" in query


def test_number_check_skips_recipient_side_sentences_max_two():
    case = _v1()
    # 받는 사람 쪽 말 꼴(자기 번호, 되묻기)의 번호는 대조하지 않는다
    case["transcript"][2] = "제 번호는 010-9999-8888 입니다. 02-7777-6666 이요?"
    case["transcript"][3] = (
        "안전계좌로 옮기셔야 합니다. 02-1234-5678 또는 02-111-2222 또는 1332 번호로 하세요"
    )
    calls = Calls()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    assert [n for n, _ in calls.numbers] == ["0212345678", "021112222"]
    assert len(result.number_checks) == 2


def test_empty_reading_on_normal_call_is_input_required_without_failures():
    case = {
        "transcript": ["여보세요", "국민은행입니다. 요청하신 통장 사본 발급이 완료되었습니다"],
        "input": {},
        "runtime": {"reading": {}},
    }
    calls = Calls()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    assert result.conclusion == Conclusion.INPUT_REQUIRED
    assert result.failures == []
    assert result.reading_status == "ok"


def _spoken_link_case(reading: dict) -> dict:
    return {
        "transcript": ["여보세요", "조회를 위해 주소창에 ykics24.kr 입력하세요"],
        "input": {},
        "runtime": {"reading": reading},
    }


@pytest.mark.parametrize(
    "reading",
    [
        {"spoken_hosts": ["ykics24.kr"]},
        {"demands": [{"kind": "visit_link", "quote": "주소창에 ykics24.kr 입력하세요"}]},
        {
            "demands": [{"kind": "visit_link", "quote": "주소창에 ykics24.kr 입력하세요"}],
            "spoken_hosts": ["ykics24.kr"],
        },
    ],
)
def test_spoken_link_is_not_input_required_and_stops_click(reading):
    case = _spoken_link_case(reading)

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert result.conclusion == Conclusion.INCONCLUSIVE
    assert result.stop_actions[0].value == "dont_click_link"
    assert result.stop_actions[-1].value == "hang_up"
    assert len(result.stop_actions) <= 3
    assert any(
        u.item.value == "link_spelling" and u.reason.value == "read_from_voice"
        for u in result.unchecked
    )
    assert result.failures == []


def test_spoken_link_with_info_request_keeps_order_and_cap():
    case = _spoken_link_case(
        {
            "spoken_hosts": ["ykics24.kr"],
            "demands": [
                {"kind": "personal_info", "quote": "주소창에 ykics24.kr 입력하세요"},
                {"kind": "money_transfer", "quote": "주소창에 ykics24.kr 입력하세요"},
            ],
        }
    )

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert [a.value for a in result.stop_actions] == [
        "dont_click_link",
        "dont_enter_info",
        "hang_up",
    ]


def test_official_contact_comes_from_kb_not_transcript():
    calls = Calls()

    result = asyncio.run(analyze_call(_transcript(_v1()), runtime=_runtime(_v1(), calls)))

    # 녹음 속 02-1234-5678 이 아니라 KB 의 금감원 대표번호
    assert result.official_contact is not None
    assert result.official_contact.org == "금융감독원"
    assert result.official_contact.number == "1332"
    assert result.official_contact.source_url.startswith("https://")


def test_official_contact_none_for_unlisted_org():
    case = yaml.safe_load((CASES_DIR / "N3.yaml").read_text(encoding="utf-8"))

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert result.official_contact is None


def test_official_contact_lookup_failure_keeps_result(monkeypatch):
    def boom(_verify):
        raise RuntimeError("kb")

    monkeypatch.setattr(voice, "_official_contact", boom)
    result = asyncio.run(analyze_call(_transcript(_v1()), runtime=_runtime(_v1(), Calls())))

    assert result.conclusion == Conclusion.SUSPECTED
    assert result.official_contact is None


def test_unlisted_org_skips_number_check():
    case = yaml.safe_load((CASES_DIR / "N3.yaml").read_text(encoding="utf-8"))
    case["transcript"][1] += " 문의는 02-1234-5678"
    calls = Calls()

    asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    assert calls.numbers == []


def test_transcript_failure_not_analyzable_without_llm():
    transcript = TranscriptResult(segments=[], failure=FailureCode.LLM_ERROR)
    calls = Calls()

    result = asyncio.run(
        analyze_call(transcript, runtime=_runtime({"runtime": {}}, calls))
    )

    assert result.conclusion is Conclusion.NOT_ANALYZABLE
    assert result.failures == [FailureCode.LLM_ERROR]
    assert calls.reading == 0 and calls.search == 0


def test_default_runtime_does_not_need_injection_for_empty():
    transcript = TranscriptResult(segments=[])

    result = asyncio.run(analyze_call(transcript))

    assert result.conclusion is Conclusion.NOT_ANALYZABLE


def test_search_timeout_continues_with_unchecked(monkeypatch):
    monkeypatch.setattr(voice, "SEARCH_BUDGET_S", 0.05)
    case = _v1()
    runtime = _runtime(case, Calls())

    def slow_search(text):
        time.sleep(0.3)
        return _COMPLETED

    runtime.search_cases = slow_search

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    assert result.similar_cases is None
    assert result.conclusion is Conclusion.SUSPECTED
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.SIMILAR_CASES, UncheckedReason.TIMEOUT) in items
    assert result.failures == []


def test_search_error_marks_failed_not_raise():
    case = _v1()
    runtime = _runtime(case, Calls())

    def broken(text):
        raise RuntimeError("boom")

    runtime.search_cases = broken

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.SIMILAR_CASES, UncheckedReason.FAILED) in items


def test_hung_reading_falls_back_to_lexicon(monkeypatch):
    monkeypatch.setattr(voice, "READ_BUDGET_S", 0.05)
    monkeypatch.setattr(voice, "_READ_GRACE_S", 0.0)
    case = _v1()
    runtime = _runtime(case, Calls())

    async def hang(source):
        await asyncio.sleep(10)

    runtime.read_claims = hang

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    assert result.conclusion is Conclusion.SUSPECTED
    assert result.failures == [FailureCode.TIMEOUT]
    assert result.reading_status == "fallback"


def _be_wrapper(deadline_s: float, margin_s: float, elapsed_s: float, runtime, transcript):
    """PR #105 콜백 경로를 줄인 모양: 앞 단계가 elapsed_s 를 쓰고, 분석부터 카드 렌더링까지를
    바깥 wait_for(deadline_s) 가 감싼다. 분석 예산은 deadline_s - margin_s - elapsed_s 다."""

    async def job():
        await asyncio.sleep(elapsed_s)  # 다운로드·STT
        result = await analyze_call(
            transcript, runtime=runtime, time_budget_s=deadline_s - margin_s - elapsed_s
        )
        await asyncio.sleep(0.05)  # 카드 렌더링·저장
        return result

    return asyncio.run(asyncio.wait_for(job(), timeout=deadline_s))


def _hanging_runtime():
    case = _v1()
    runtime = _runtime(case, Calls())

    async def hang(source):
        await asyncio.sleep(10)

    runtime.read_claims = hang
    return case, runtime


# 시간을 줄여 본다(상한 1.5초, 앞 단계 0.2초). analyze_call 은 예산을 넘겨도 수십 ms(실측 최대 약 45ms)라
# 여유는 그보다 넉넉히 0.3초로 둔다. 실제 값(상한 45초, 여유 5초)에서는 차이가 더 크다.
_DEADLINE_S = 1.5
_ELAPSED_S = 0.2


def test_budget_with_margin_finishes_inside_be_deadline(monkeypatch):
    """지침의 40 - elapsed (= 상한 - 여유 - elapsed). 읽기가 멈춰도 상한 안에 카드까지 나온다."""
    monkeypatch.setattr(voice, "READ_BUDGET_S", 12.0)  # 읽기 한도가 아니라 예산이 끝을 정하게
    case, runtime = _hanging_runtime()

    result = _be_wrapper(_DEADLINE_S, 0.3, _ELAPSED_S, runtime, _transcript(case))

    assert result.conclusion is Conclusion.SUSPECTED
    assert FailureCode.TIMEOUT in result.failures


def test_budget_without_margin_is_cancelled_by_be_deadline(monkeypatch):
    """45 - elapsed 로 주면 분석이 상한을 다 써서 카드 렌더링 전에 바깥 wait_for 가 취소한다."""
    monkeypatch.setattr(voice, "READ_BUDGET_S", 12.0)
    case, runtime = _hanging_runtime()

    with pytest.raises(TimeoutError):
        _be_wrapper(_DEADLINE_S, 0.0, _ELAPSED_S, runtime, _transcript(case))


def test_reading_exception_falls_back_to_lexicon():
    case = _v1()
    runtime = _runtime(case, Calls())

    async def boom(source):
        raise RuntimeError("boom")

    runtime.read_claims = boom

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    assert result.failures == [FailureCode.LLM_ERROR]
    assert result.conclusion is Conclusion.SUSPECTED


def test_exhausted_budget_skips_reading_with_timeout():
    case = _v1()
    calls = Calls()

    result = asyncio.run(
        analyze_call(_transcript(case), runtime=_runtime(case, calls), time_budget_s=0)
    )

    assert result.conclusion is Conclusion.NOT_ANALYZABLE
    assert calls.reading == 0
    assert FailureCode.TIMEOUT in result.failures
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.TRANSCRIPT, UncheckedReason.TIMEOUT) in items


def test_budget_spent_during_reading_marks_phone_number_timeout():
    case = _v1()
    calls = Calls()
    runtime = _runtime(case, calls)
    inner = runtime.read_claims

    async def slow_read(source):
        await asyncio.sleep(0.1)
        return await inner(source)

    runtime.read_claims = slow_read

    result = asyncio.run(
        analyze_call(_transcript(case), runtime=runtime, time_budget_s=0.05)
    )

    assert calls.numbers == []
    assert result.number_checks == []
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.PHONE_NUMBER, UncheckedReason.TIMEOUT) in items


def test_cancellation_propagates():
    case = _v1()
    runtime = _runtime(case, Calls())

    async def never(source):
        await asyncio.sleep(10)

    runtime.read_claims = never

    async def run():
        task = asyncio.create_task(analyze_call(_transcript(case), runtime=runtime))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_warm_up_voice_case_search_fills_index():
    assert warm_up_voice_case_search().status is AnalysisStatus.COMPLETED


def test_mask_failure_returns_not_analyzable_without_reading(monkeypatch):
    def boom(t):
        raise RuntimeError("mask")

    monkeypatch.setattr(voice, "mask_transcript", boom)
    case = _v1()
    calls = Calls()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, calls)))

    assert result.conclusion is Conclusion.NOT_ANALYZABLE
    assert FailureCode.MISSING_RESULT in result.failures
    assert calls.reading == 0 and calls.search == 0
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.TRANSCRIPT, UncheckedReason.FAILED) in items


def test_verify_failure_returns_not_analyzable(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("verify")

    monkeypatch.setattr(voice, "verify_call", boom)
    case = _v1()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert result.conclusion is Conclusion.NOT_ANALYZABLE
    assert FailureCode.MISSING_RESULT in result.failures


def test_decide_call_failure_returns_minimal_result(monkeypatch):
    def boom(**k):
        raise RuntimeError("gate")

    monkeypatch.setattr(voice, "decide_call", boom)
    case = _v1()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert result.conclusion is Conclusion.NOT_ANALYZABLE
    assert result.failures == [FailureCode.MISSING_RESULT]


def test_number_text_failure_still_returns_result(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("numbers")

    monkeypatch.setattr(voice, "find_numbers", boom)
    case = _v1()

    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert result.conclusion is Conclusion.SUSPECTED
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.PHONE_NUMBER, UncheckedReason.FAILED) in items


def test_phone_check_error_marks_phone_number_failed():
    case = _v1()
    runtime = _runtime(case, Calls())

    def broken(number, org):
        raise RuntimeError("kb")

    runtime.check_phone_number = broken

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    assert result.conclusion is Conclusion.SUSPECTED
    assert result.number_checks == []
    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.PHONE_NUMBER, UncheckedReason.FAILED) in items
    assert (UncheckedItem.PHONE_NUMBER, UncheckedReason.NOT_APPLICABLE) not in items


def test_search_by_identity_prefers_family_types_only_with_acquaintance_words(monkeypatch):
    from ai.case import voice

    seen = []
    monkeypatch.setattr(
        voice, "search_voice_cases", lambda text, preferred_types=(), **_: seen.append(set(preferred_types))
    )
    voice.search_by_identity("엄마 나 폰 고장나서 문자해")
    voice.search_by_identity("안녕하세요 택배입니다")
    voice.search_by_identity("서울지검 검사입니다")

    assert seen == [{"family_impersonation", "kidnapping"}, set(), set()]


def test_default_runtime_search_is_identity_aware_and_gated():
    from ai.case import voice

    assert voice.VoiceRuntime().search_cases is voice.search_gated


_FOUND = CaseSearchResult(
    status=AnalysisStatus.COMPLETED,
    matches=[CaseMatch(case_id="VC-0001", similarity=0.2, matched_variant="대본")],
)


def _gate(monkeypatch, score):
    calls = []

    def fake_score(text):
        calls.append(text)
        if isinstance(score, Exception):
            raise score
        return score

    monkeypatch.setattr(voice, "search_by_identity", lambda text, **_: _FOUND)
    monkeypatch.setattr(voice, "attach_score", fake_score)
    return calls


def test_gate_below_min_drops_cases_but_keeps_completed(monkeypatch):
    _gate(monkeypatch, voice.search.VOICE_GATE_MIN - 0.01)

    result = voice.search_gated("대본")

    assert result.status is AnalysisStatus.COMPLETED and result.matches == []


def test_gate_at_or_above_min_keeps_cases(monkeypatch):
    _gate(monkeypatch, voice.search.VOICE_GATE_MIN)

    assert voice.search_gated("대본") == _FOUND


def test_gate_is_skipped_when_nothing_to_attach(monkeypatch):
    calls = _gate(monkeypatch, 0.0)
    monkeypatch.setattr(voice, "search_by_identity", lambda text, **_: _COMPLETED)

    assert voice.search_gated("대본") == _COMPLETED
    assert calls == []


def test_gate_failure_is_unchecked_failed_and_decision_unchanged(monkeypatch):
    case = _v1()
    baseline = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))
    _gate(monkeypatch, RuntimeError("model broken"))
    runtime = _runtime(case, Calls())
    runtime.search_cases = voice.search_gated

    result = asyncio.run(analyze_call(_transcript(case), runtime=runtime))

    items = {(u.item, u.reason) for u in result.unchecked}
    assert (UncheckedItem.SIMILAR_CASES, UncheckedReason.FAILED) in items
    assert result.similar_cases is None
    # 관문은 붙일지만 정한다. 결론·근거는 사례 검색이 비었을 때와 같다.
    assert result.conclusion is baseline.conclusion
    assert result.evidence == baseline.evidence


@pytest.mark.parametrize("path", CASE_FILES, ids=lambda p: p.stem)
def test_gate_drop_does_not_change_conclusion(monkeypatch, path):
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    _gate(monkeypatch, voice.search.VOICE_GATE_MIN + 0.05)
    attached = _runtime(case, Calls())
    attached.search_cases = voice.search_gated
    with_cases = asyncio.run(analyze_call(_transcript(case), runtime=attached))
    _gate(monkeypatch, voice.search.VOICE_GATE_MIN - 0.05)
    dropped = _runtime(case, Calls())
    dropped.search_cases = voice.search_gated
    without = asyncio.run(analyze_call(_transcript(case), runtime=dropped))

    assert getattr(without.similar_cases, "matches", []) == []  # 검색을 안 하는 입력도 있다
    assert with_cases.conclusion is without.conclusion
    assert with_cases.evidence == without.evidence


def test_warm_up_fills_index_even_if_gate_model_fails(monkeypatch):
    called = []
    monkeypatch.setattr(voice.search, "warm_up_voice_case_search", lambda: called.append("index") or _COMPLETED)

    def broken():
        raise OSError("missing model")

    monkeypatch.setattr(voice, "load_model", broken)

    assert voice.warm_up_voice_case_search() == _COMPLETED
    assert called == ["index"]


@pytest.mark.parametrize(
    ("line", "suspected"),
    [
        ("경찰입니다. 돈을 보내지 마세요", False),
        ("경찰입니다. 앱을 깔지 마세요", False),
        ("경찰입니다. 안내에 따라 안전계좌로 송금해 주세요", True),
    ],
)
def test_lexicon_fallback_respects_which_action_is_negated(line, suspected):
    # PR #114 리뷰: 어휘표 폴백에서 "보내지 마세요" 가 요구로 남아 설명이 거꾸로 나왔다
    case = {"transcript": [line]}
    result = asyncio.run(analyze_call(_transcript(case), runtime=_runtime(case, Calls())))

    assert (result.conclusion is Conclusion.SUSPECTED) is suspected
