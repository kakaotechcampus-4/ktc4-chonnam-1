"""링크 없는 문자(가족·지인·기관 사칭) 분석 진입 함수 (v3 §2·§8, 세 번째 시나리오).

통화 분석(voice.py)과 같은 읽기·검증을 쓰고 게이트만 문자용(message_gate.py)이다.
어느 단계가 실패해도 예외를 던지지 않고 `failures`·`unchecked` 에 남겨 결과를 낸다
(CancelledError 만 올린다). 답 처리(apply_message_answer)는 LLM·네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai.diagnostics import where
from ai.case.actions import response_actions_for
from ai.case.lexicon import ACCOUNT_LOCK_PATTERN, LOTTERY_PATTERN, read_with_lexicon
from ai.case.message_gate import MESSENGER_HANDLE, Q1_ACCEPTED, Q2_OPTIONS, Q2_SETTLED, decide_message
from ai.case.privacy import mask_pii
from ai.case.types import (
    CheckItem,
    ClaimReadingResult,
    Evidence,
    EvidenceCode,
    InputKind,
    MessageCaseResult,
    Origin,
    QuestionId,
    ReadingSource,
    Scenario,
    Segment,
    Side,
    UncheckedItem,
    UncheckedReason,
)
from ai.case.verify import verify_call
from ai.kb.search import search_cases
from ai.case.claims import TIMEOUT_SECONDS as READ_BUDGET_S
from ai.case.steps import READ_GRACE_S as _READ_GRACE_S
from ai.case.steps import SEARCH_BUDGET_S, official_contact_for, read_or_lexicon, search_within
from ai.case.claims import read_claims
from ai.types import CaseSearchResult, FailureCode

LOGGER = logging.getLogger(__name__)

DEFAULT_TIME_BUDGET_S = 25.0
# read_message 의 MAX_MESSAGE_CHARS 와 같다. 더 긴 글은 BE 가 먼저 막는다
MAX_MESSAGE_CHARS = 8192


@dataclass
class MessageRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    read_claims: Callable[[ReadingSource], Awaitable[ClaimReadingResult]] = read_claims
    # 동기 함수라 asyncio.to_thread 로 부른다. 문자 사례 KB(case_examples)를 쓴다
    search_cases: Callable[[str], CaseSearchResult] = search_cases


# "<받은 문자> 라고 (문자가) 왔는데 <내 질문>" 꼴(#143). 받은 문자를 문장 안에 넣어 보내면 줄 전체가
# 받는 사람의 말로 읽혀 그 안의 요구·사정이 버려진다. 받은 문자와 질문을 다른 세그먼트로 나눈다
_REPORTED = re.compile(
    r"^(?P<message>.+?)\s*(?:이?라고|이렇게|하고)\s*"
    r"(?:(?:문자|메시지|메세지|카톡|톡|연락)\s*(?:가|이)?\s*)?"
    r"(?:왔는데|왔어요|왔어|왔습니다|왔네요|왔다|옴|받았는데|받았어요)[.,!?…]*\s*(?P<rest>.*)$"
)


def _split_reported(line: str) -> list[str]:
    match = _REPORTED.match(line)
    if match is None:
        return [line]
    return [part for part in (match["message"].strip(), match["rest"].strip()) if part]


def message_source(text: str) -> ReadingSource:
    """마스킹한 문자를 줄 단위 세그먼트로 만든다. 문자는 보낸 사람 한 명의 말이라 화자를 나누지 않는다.

    "~라고 왔는데 ~맞을까?" 처럼 받은 문자를 문장 안에 넣은 줄은 받은 문자와 질문으로 나눈다(#143).
    """
    lines = [part for line in mask_pii(text).splitlines() for part in _split_reported(line.strip())]
    return ReadingSource(
        kind=InputKind.TEXT,
        segments=[Segment(side=Side.UNKNOWN, text=line) for line in lines if line],
    )


async def analyze_message_case(
    text: str,
    *,
    runtime: MessageRuntime | None = None,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    has_link: bool = False,
) -> MessageCaseResult:
    """링크 없는 문자를 분석한다. 질문으로 멈추지 않고 늘 결과를 낸다.

    has_link=True 면 링크가 있는 문자의 본문을 같은 규칙으로 읽는다(#89). BE 는 링크 결과가 판단
    보류일 때만 부르며, 이 결과로 판단 보류 카드의 주의 갈래를 고른다. 링크 판정은 바꾸지 않는다.

    BE 는 링크가 없다고 확인된 문자(read_message 가 성공했고 links·unverified_links 가 비었음)만
    넘긴다. 예기치 못한 오류가 나도 예외 대신 분석 불가 결과를 돌려준다(CancelledError 만 올린다).
    """
    try:
        return await _analyze(text, runtime or MessageRuntime(), time_budget_s, has_link)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 분석의 맨 바깥이다. 예외 대신 분석 불가 결과를 돌려준다 (원칙 3)
        LOGGER.warning("message analysis failed: %s", where(exc))
        return decide_message(accepted=None, failures=[FailureCode.MISSING_RESULT])


async def _analyze(
    text: str, runtime: MessageRuntime, time_budget_s: float, has_link: bool = False
) -> MessageCaseResult:
    started = time.monotonic()

    def remaining() -> float:
        return max(time_budget_s - (time.monotonic() - started), 0.0)

    if not isinstance(text, str) or not text.strip():
        # 읽을 글이 없으면 LLM 을 부르지 않는다. 읽지 않았으니 문자 내용을 확인한 것으로 세지 않는다
        empty = decide_message(accepted=[], failures=[FailureCode.EMPTY_INPUT])
        return empty.model_copy(
            update={"checked": [c for c in empty.checked if c != CheckItem.MESSAGE_TEXT]}
        )
    if len(text) > MAX_MESSAGE_CHARS:
        return decide_message(accepted=None, failures=[FailureCode.INPUT_TOO_LARGE])

    try:
        source = message_source(text)
    except Exception as exc:  # noqa: BLE001 — 가리지 못한 글은 읽기·검색에 넘기지 않고 분석 불가로 끝낸다
        # 가리지 못한 문자는 읽기·검색에 넘기지 않는다
        LOGGER.warning("message masking failed: %s", where(exc))
        return decide_message(accepted=None, failures=[FailureCode.MISSING_RESULT])

    failures: list[FailureCode] = []
    if remaining() <= 0:
        # 예산이 없으면 LLM 없이 어휘표만 쓴다
        reading = read_with_lexicon(source, FailureCode.TIMEOUT)
        similar, search_miss = None, UncheckedReason.TIMEOUT
    else:
        query = "\n".join(seg.text for seg in source.segments)
        reading, (similar, search_miss) = await asyncio.gather(
            read_or_lexicon(
                runtime.read_claims, source, min(READ_BUDGET_S + _READ_GRACE_S, remaining()), label="message",
            ),
            search_within(runtime.search_cases, query, min(SEARCH_BUDGET_S, remaining()), label="message"),
        )

    if reading.reading is None:
        # 읽기 결과가 비어 오면 어휘표로 메운다
        reading = read_with_lexicon(source, reading.failure or FailureCode.INVALID_OUTPUT)
    if reading.failure is not None:
        failures.append(reading.failure)

    try:
        verify = verify_call(
            source,
            reading.reading,
            derived_by="lexicon" if reading.status == "fallback" else "llm",
            origin=Origin.MESSAGE,
        )
    except Exception as exc:  # noqa: BLE001 — 검증이 깨지면 읽은 주장을 쓰지 않고 분석 불가로 끝낸다
        LOGGER.warning("message claims verification failed: %s", where(exc))
        return decide_message(
            accepted=None,
            similar=similar,
            search_miss=search_miss,
            failures=[*failures, FailureCode.MISSING_RESULT],
        )

    try:
        contact = official_contact_for(verify)
    except Exception as exc:  # noqa: BLE001 — 확인 안내는 부가 정보다. 못 찾아도 결론은 낸다
        # 확인 안내는 부가 정보다. 못 찾으면 FE 가 일반 문장을 쓴다
        LOGGER.warning("official contact lookup failed: %s", where(exc))
        contact = None

    return decide_message(
        accepted=_with_lock_lure(source, _with_handle_line(source, verify.accepted), has_link),
        claims=verify.claims,
        rejected=verify.rejected,
        similar=similar,
        search_miss=search_miss,
        official_contact=contact,
        failures=failures,
        reading_status=reading.status,
        has_link=has_link,
    )


# 링크가 있는 문자에서만 쓰는 미끼 어휘표와 근거 코드 (#160)
_LINK_LURES = (
    (ACCOUNT_LOCK_PATTERN, EvidenceCode.ACCOUNT_LOCK_LURE),
    (LOTTERY_PATTERN, EvidenceCode.LOTTERY_LURE),
)


def _with_lock_lure(
    source: ReadingSource, accepted: list[Evidence], has_link: bool
) -> list[Evidence]:
    """링크가 있는 문자에서 미끼 줄을 어휘표로 찾아 근거로 덧붙인다 (#160-2·4).

    LLM 은 "일시동결 되었습니다" 를 명분으로만 읽고 "해지해주세요" 를 요구로 읽지 않으며,
    "[로)1179회 2,등 발송오류" 같은 로또 미끼는 주장·요구가 없어 아무것도 읽지 않는다.
    판정은 이 근거를 보고 게이트가 한다. 링크가 없으면 쓰지 않는다(링크로 끌어들이는 수법이다).
    미끼 종류마다 첫 줄 하나만 근거로 남긴다.
    """
    if not has_link:
        return accepted
    found = []
    for pattern, code in _LINK_LURES:
        line = next((seg.text for seg in source.segments if pattern.search(seg.text)), None)
        if line is not None:
            found.append(Evidence(
                code=code, origin=Origin.MESSAGE, quote=line,
                derived_by="lexicon", counts_for_floor=False,
            ))
    return [*accepted, *found]


def _with_handle_line(source: ReadingSource, accepted: list[Evidence]) -> list[Evidence]:
    """메신저 이동 인용에 아이디가 빠졌으면 같은 줄의 아이디를 근거로 덧붙인다.

    LLM 은 "제 라인 추가해 주세요" 만 인용하고 같은 줄 끝의 "zxf226" 을 빼기도 한다.
    게이트는 근거만 보고 다시 돌므로(답 처리) 아이디가 든 줄을 근거로 남겨 둔다.
    """
    moves = [e for e in accepted if e.code == EvidenceCode.MESSENGER_MOVE and e.quote]
    if not moves or any(MESSENGER_HANDLE.search(e.quote) for e in moves):
        return accepted
    for seg in source.segments:
        if MESSENGER_HANDLE.search(seg.text) and any(e.quote in seg.text for e in moves):
            return [*accepted, Evidence(
                code=EvidenceCode.MESSENGER_MOVE, origin=Origin.MESSAGE, quote=seg.text,
                derived_by="code", counts_for_floor=False,
            )]
    return accepted


def _search_miss(result: MessageCaseResult) -> UncheckedReason | None:
    """처음 분석 때 검색이 실패했는지 결과에서 되찾는다. 답 처리 뒤에도 같은 줄을 남기려는 것이다."""
    for entry in result.unchecked:
        if entry.item == UncheckedItem.SIMILAR_CASES and entry.reason in (
            UncheckedReason.TIMEOUT,
            UncheckedReason.FAILED,
        ):
            return entry.reason
    return None


def apply_message_answer(
    result: MessageCaseResult, question_id: str, option_id: str
) -> MessageCaseResult:
    """Q1·Q2 답을 넣고 게이트를 다시 돌린 사본을 돌려준다 (v3 §8). LLM·네트워크를 쓰지 않는다.

    - Q1(already_acted): 답이 쌓이고 response_actions 만 다시 정해진다. 결론은 그대로다.
    - Q2(called_known_number): 마지막 답으로 덮어쓴다. not_person 이면 L-4 로 결론이 바뀔 수 있다.
    - 모르는 질문·선택지에는 아무것도 바꾸지 않고 그대로 돌려준다.
    - Q2 가 본인이 아니다·맞다로 정해진 뒤에 온 Q2 답은 무시한다. 대화창에 남은 예전 카드를 다시
      눌러 결론이 '안전' 쪽으로 돌아가지 않게 하려는 것이다.
    """
    answers = result.answers
    if question_id == QuestionId.ALREADY_ACTED.value and option_id in Q1_ACCEPTED:
        acted = list(answers.already_acted)
        if option_id not in acted:
            acted.append(option_id)
        answers = answers.model_copy(update={"already_acted": acted})
    elif question_id == QuestionId.CALLED_KNOWN_NUMBER.value and option_id in Q2_OPTIONS:
        if answers.called_known_number in Q2_SETTLED:
            return result
        answers = answers.model_copy(update={"called_known_number": option_id})
    else:
        return result

    # 분석하지 못한 결과(not_analyzable)는 다시 돌릴 근거가 없다. 답을 남기고, 돈을 보냈다는
    # 사람에게는 결론과 상관없이 대응을 준다 (v3 §8)
    if result.conclusion.value == "not_analyzable":
        return result.model_copy(
            update={
                "answers": answers,
                "response_actions": response_actions_for(answers.already_acted, [], call=False),
            }
        )

    return decide_message(
        accepted=result.evidence,
        claims=result.claims,
        rejected=result.rejected,
        similar=result.similar_cases,
        search_miss=_search_miss(result),
        official_contact=result.official_contact,
        answers=answers,
        failures=result.failures,
        reading_status=result.reading_status,
        has_link=result.scenario == Scenario.LINK_SMISHING,
    )
