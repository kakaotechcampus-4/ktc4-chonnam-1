"""통화 분석 진입 함수 (v3 §6.4, 스펙 D1~D8).

읽기·검색·번호 대조를 묶어 게이트에 넘긴다. 어느 단계가 실패해도 예외를 던지지 않고
`failures`·`unchecked` 에 남겨 결과를 낸다(CancelledError 만 올린다).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai.case.gate import decide_call
from ai.case.lexicon import read_with_lexicon
from ai.case.privacy import mask_transcript
from ai.case.types import (
    ClaimReadingResult,
    InputKind,
    NumberCheck,
    ReadingSource,
    Side,
    TranscriptResult,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VoiceCaseResult,
)
from ai.case.verify import VerifyResult, verify_call
from ai.kb.numbers import check_phone_number, find_numbers, resolve_org
from ai.kb.search import search_voice_cases
from ai.llm.claims import TIMEOUT_SECONDS as READ_BUDGET_S
from ai.llm.claims import read_claims
from ai.types import CaseSearchResult, FailureCode

LOGGER = logging.getLogger(__name__)

SEARCH_BUDGET_S = 1.0
DEFAULT_TIME_BUDGET_S = 40.0
# read_claims 가 제 시간 초과에서 스스로 어휘표로 폴백하도록, 바깥 한도는 조금 넉넉히 둔다
_READ_GRACE_S = 0.5
MAX_NUMBER_CHECKS = 2


@dataclass
class VoiceRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    read_claims: Callable[[ReadingSource], Awaitable[ClaimReadingResult]] = read_claims
    # 동기 함수라 asyncio.to_thread 로 부른다
    search_cases: Callable[[str], CaseSearchResult] = search_voice_cases
    check_phone_number: Callable[[str, str], NumberCheck] = check_phone_number


def _is_empty(transcript: TranscriptResult) -> bool:
    return transcript.failure is not None or not any(
        seg.text.strip() for seg in transcript.segments
    )


def _set_unchecked(
    result: VoiceCaseResult, item: UncheckedItem, reason: UncheckedReason
) -> None:
    """게이트가 넣은 같은 항목의 이유를 바꾼다. 없으면 덧붙인다."""
    entry = Unchecked(item=item, reason=reason)
    for i, old in enumerate(result.unchecked):
        if old.item == item:
            result.unchecked[i] = entry
            return
    result.unchecked.append(entry)


async def _search(
    runtime: VoiceRuntime, text: str, timeout_s: float
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
        LOGGER.warning("voice case search failed: %s", type(exc).__name__)
        return None, UncheckedReason.FAILED


async def _read(
    runtime: VoiceRuntime, source: ReadingSource, timeout_s: float
) -> ClaimReadingResult:
    try:
        return await asyncio.wait_for(runtime.read_claims(source), timeout=timeout_s)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        failure = FailureCode.TIMEOUT
    except Exception as exc:
        LOGGER.warning("voice claims reading failed: %s", type(exc).__name__)
        failure = FailureCode.LLM_ERROR
    return read_with_lexicon(source, failure)


def _check_numbers(
    runtime: VoiceRuntime, source: ReadingSource, verify: VerifyResult
) -> list[NumberCheck]:
    """받아들인 기관 주장이 KB 에 있고 상대 발화에 번호가 있으면 번호마다 대조한다."""
    if verify.speaker_split_failed or verify.other_speaker is None:
        # 누구의 말인지 몰라 상대가 말한 번호라고 할 수 없다
        return []
    org = None
    for ident in verify.claims.identity:
        if ident.listed_institution:
            org = resolve_org(ident.name) or resolve_org(ident.quote)
            if org:
                break
    if org is None:
        return []
    other_text = " ".join(
        seg.text for seg in source.segments if seg.side == Side.OTHER
    )
    checks: list[NumberCheck] = []
    for number in find_numbers(other_text)[:MAX_NUMBER_CHECKS]:
        try:
            checks.append(runtime.check_phone_number(number, org))
        except Exception as exc:
            LOGGER.warning("phone number check failed: %s", type(exc).__name__)
    return checks


def _fill_sides(source: ReadingSource, verify: VerifyResult) -> None:
    """읽기 뒤에 세그먼트의 side 를 채운다. 화자를 모르면 unknown 으로 둔다."""
    other = verify.other_speaker
    for seg in source.segments:
        if verify.speaker_split_failed or other is None or seg.speaker is None:
            seg.side = Side.UNKNOWN
        else:
            seg.side = Side.OTHER if seg.speaker == other else Side.SELF


async def analyze_call(
    transcript: TranscriptResult,
    *,
    runtime: VoiceRuntime | None = None,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
) -> VoiceCaseResult:
    """STT·화자 분리 결과를 분석한다. 질문으로 멈추지 않고 늘 결과를 낸다."""
    runtime = runtime or VoiceRuntime()
    started = time.monotonic()

    def remaining() -> float:
        return time_budget_s - (time.monotonic() - started)

    failures: list[FailureCode] = []
    if transcript.failure is not None:
        failures.append(transcript.failure)

    # U-6: 읽을 것이 없으면 LLM 을 부르지 않는다
    if _is_empty(transcript):
        return decide_call(
            transcript_empty=True,
            verify=None,
            number_checks=[],
            similar=None,
            failures=failures,
        )

    masked = mask_transcript(transcript)
    source = ReadingSource(
        kind=InputKind.AUDIO,
        segments=masked.segments,
        speaker_split_failed=transcript.speaker_split_failed,
    )

    if remaining() <= 0:
        result = decide_call(
            transcript_empty=False,
            verify=None,
            number_checks=[],
            similar=None,
            failures=[*failures, FailureCode.TIMEOUT],
        )
        _set_unchecked(result, UncheckedItem.TRANSCRIPT, UncheckedReason.TIMEOUT)
        return result

    # 사례 데이터에 화자 표기가 없어서 질의는 마스킹 대본 전체다 (Task 6)
    query = "\n".join(seg.text for seg in source.segments)
    reading, (similar, search_miss) = await asyncio.gather(
        _read(
            runtime, source, min(READ_BUDGET_S + _READ_GRACE_S, max(remaining(), 0.0))
        ),
        _search(runtime, query, min(SEARCH_BUDGET_S, max(remaining(), 0.0))),
    )

    if reading.reading is None:
        # 읽기 결과가 비어 오면 어휘표로 메운다
        reading = read_with_lexicon(source, reading.failure or FailureCode.INVALID_OUTPUT)
    if reading.failure is not None:
        failures.append(reading.failure)
    verify = verify_call(
        source,
        reading.reading,
        derived_by="lexicon" if reading.status == "fallback" else "llm",
    )
    _fill_sides(source, verify)

    number_timeout = remaining() <= 0
    number_checks = [] if number_timeout else _check_numbers(runtime, source, verify)

    result = decide_call(
        transcript_empty=False,
        verify=verify,
        number_checks=number_checks,
        similar=similar,
        failures=failures,
        reading_status=reading.status,
    )
    if search_miss is not None:
        _set_unchecked(result, UncheckedItem.SIMILAR_CASES, search_miss)
    if number_timeout:
        _set_unchecked(result, UncheckedItem.PHONE_NUMBER, UncheckedReason.TIMEOUT)
    return result
