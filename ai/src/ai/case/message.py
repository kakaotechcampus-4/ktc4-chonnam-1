"""링크 없는 문자(가족·지인·기관 사칭) 분석 진입 함수 (v3 §2·§8, 세 번째 시나리오).

통화 분석(voice.py)과 같은 읽기·검증을 쓰고 게이트만 문자용(message_gate.py)이다.
어느 단계가 실패해도 예외를 던지지 않고 `failures`·`unchecked` 에 남겨 결과를 낸다
(CancelledError 만 올린다). 답 처리(apply_message_answer)는 LLM·네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai.case.lexicon import read_with_lexicon
from ai.case.message_gate import Q1_ACCEPTED, Q2_OPTIONS, decide_message
from ai.case.privacy import mask_pii
from ai.case.types import (
    ClaimReadingResult,
    InputKind,
    MessageCaseResult,
    OfficialContact,
    Origin,
    QuestionId,
    ReadingSource,
    Segment,
    Side,
    UncheckedItem,
    UncheckedReason,
)
from ai.case.verify import VerifyResult, verify_call
from ai.kb.numbers import official_contact
from ai.kb.search import search_cases
from ai.llm.claims import TIMEOUT_SECONDS as READ_BUDGET_S
from ai.llm.claims import read_claims
from ai.types import CaseSearchResult, FailureCode

LOGGER = logging.getLogger(__name__)

SEARCH_BUDGET_S = 1.0
DEFAULT_TIME_BUDGET_S = 25.0
# read_claims 가 제 시간 초과에서 스스로 어휘표로 폴백하도록, 바깥 한도는 조금 넉넉히 둔다
_READ_GRACE_S = 0.5
# read_message 의 MAX_MESSAGE_CHARS 와 같다. 더 긴 글은 BE 가 먼저 막는다
MAX_MESSAGE_CHARS = 8192


@dataclass
class MessageRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    read_claims: Callable[[ReadingSource], Awaitable[ClaimReadingResult]] = read_claims
    # 동기 함수라 asyncio.to_thread 로 부른다. 문자 사례 KB(case_examples)를 쓴다
    search_cases: Callable[[str], CaseSearchResult] = search_cases


def message_source(text: str) -> ReadingSource:
    """마스킹한 문자를 줄 단위 세그먼트로 만든다. 문자는 보낸 사람 한 명의 말이라 화자를 나누지 않는다."""
    lines = [line.strip() for line in mask_pii(text).splitlines()]
    return ReadingSource(
        kind=InputKind.TEXT,
        segments=[Segment(side=Side.UNKNOWN, text=line) for line in lines if line],
    )


async def _search(
    runtime: MessageRuntime, text: str, timeout_s: float
) -> tuple[CaseSearchResult | None, UncheckedReason | None]:
    try:
        found = await asyncio.wait_for(
            asyncio.to_thread(runtime.search_cases, text), timeout=timeout_s
        )
        return found, None
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return None, UncheckedReason.TIMEOUT
    except Exception as exc:
        LOGGER.warning("message case search failed: %s", type(exc).__name__)
        return None, UncheckedReason.FAILED


async def _read(
    runtime: MessageRuntime, source: ReadingSource, timeout_s: float
) -> ClaimReadingResult:
    try:
        return await asyncio.wait_for(runtime.read_claims(source), timeout=timeout_s)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        failure = FailureCode.TIMEOUT
    except Exception as exc:
        LOGGER.warning("message claims reading failed: %s", type(exc).__name__)
        failure = FailureCode.LLM_ERROR
    return read_with_lexicon(source, failure)


def _official_contact(verify: VerifyResult) -> OfficialContact | None:
    """받아들인 기관 주장 중 대표번호 KB 에 있는 첫 기관. 번호는 KB 에서만 온다."""
    for ident in verify.claims.identity:
        if ident.listed_institution:
            contact = official_contact(ident.name) or official_contact(ident.quote)
            if contact:
                return contact
    return None


async def analyze_message_case(
    text: str,
    *,
    runtime: MessageRuntime | None = None,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
) -> MessageCaseResult:
    """링크 없는 문자를 분석한다. 질문으로 멈추지 않고 늘 결과를 낸다.

    BE 는 링크가 없다고 확인된 문자(read_message 가 성공했고 links·unverified_links 가 비었음)만
    넘긴다. 예기치 못한 오류가 나도 예외 대신 분석 불가 결과를 돌려준다(CancelledError 만 올린다).
    """
    try:
        return await _analyze(text, runtime or MessageRuntime(), time_budget_s)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.warning("message analysis failed: %s", type(exc).__name__)
        return decide_message(accepted=None, failures=[FailureCode.MISSING_RESULT])


async def _analyze(
    text: str, runtime: MessageRuntime, time_budget_s: float
) -> MessageCaseResult:
    started = time.monotonic()

    def remaining() -> float:
        return max(time_budget_s - (time.monotonic() - started), 0.0)

    if not isinstance(text, str) or not text.strip():
        # 읽을 글이 없으면 LLM 을 부르지 않는다
        return decide_message(accepted=[], failures=[FailureCode.EMPTY_INPUT])
    if len(text) > MAX_MESSAGE_CHARS:
        return decide_message(accepted=None, failures=[FailureCode.INPUT_TOO_LARGE])

    try:
        source = message_source(text)
    except Exception as exc:
        # 가리지 못한 문자는 읽기·검색에 넘기지 않는다
        LOGGER.warning("message masking failed: %s", type(exc).__name__)
        return decide_message(accepted=None, failures=[FailureCode.MISSING_RESULT])

    failures: list[FailureCode] = []
    if remaining() <= 0:
        # 예산이 없으면 LLM 없이 어휘표만 쓴다
        reading = read_with_lexicon(source, FailureCode.TIMEOUT)
        similar, search_miss = None, UncheckedReason.TIMEOUT
    else:
        query = "\n".join(seg.text for seg in source.segments)
        reading, (similar, search_miss) = await asyncio.gather(
            _read(runtime, source, min(READ_BUDGET_S + _READ_GRACE_S, remaining())),
            _search(runtime, query, min(SEARCH_BUDGET_S, remaining())),
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
    except Exception as exc:
        LOGGER.warning("message claims verification failed: %s", type(exc).__name__)
        return decide_message(
            accepted=None,
            similar=similar,
            search_miss=search_miss,
            failures=[*failures, FailureCode.MISSING_RESULT],
        )

    try:
        contact = _official_contact(verify)
    except Exception as exc:
        # 확인 안내는 부가 정보다. 못 찾으면 FE 가 일반 문장을 쓴다
        LOGGER.warning("official contact lookup failed: %s", type(exc).__name__)
        contact = None

    return decide_message(
        accepted=verify.accepted,
        claims=verify.claims,
        rejected=verify.rejected,
        similar=similar,
        search_miss=search_miss,
        official_contact=contact,
        failures=failures,
        reading_status=reading.status,
    )


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
    - 모르는 질문·선택지, 분석하지 못한 결과에는 아무것도 바꾸지 않고 그대로 돌려준다.
    """
    answers = result.answers
    if question_id == QuestionId.ALREADY_ACTED.value and option_id in Q1_ACCEPTED:
        acted = list(answers.already_acted)
        if option_id not in acted:
            acted.append(option_id)
        answers = answers.model_copy(update={"already_acted": acted})
    elif question_id == QuestionId.CALLED_KNOWN_NUMBER.value and option_id in Q2_OPTIONS:
        answers = answers.model_copy(update={"called_known_number": option_id})
    else:
        return result

    # 분석하지 못한 결과(not_analyzable)는 다시 돌릴 근거가 없다. 답만 남긴다
    if result.conclusion.value == "not_analyzable":
        return result.model_copy(update={"answers": answers})

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
    )
