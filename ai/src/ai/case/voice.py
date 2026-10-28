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

from ai.case.attach_gate import attach_score, load_model
from ai.case.gate import decide_call
from ai.case.lexicon import identity_kinds, read_with_lexicon
from ai.case.privacy import mask_transcript
from ai.case.types import (
    ClaimReadingResult,
    Conclusion,
    IdentityKind,
    InputKind,
    NumberCheck,
    ReadingSource,
    Scenario,
    Side,
    TranscriptResult,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VoiceCaseResult,
)
from ai.case.verify import VerifyResult, verify_call
from ai.kb import search
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


# 신원 어휘 종류별로 앞세울 통화 사례 유형. 어휘표가 내는 종류만 쓴다(기관은 사례가 많아 이득이 없었다).
IDENTITY_PREFERRED_TYPES: dict[IdentityKind, frozenset[str]] = {
    IdentityKind.ACQUAINTANCE: frozenset({"family_impersonation", "kidnapping"}),
}


def search_by_identity(transcript: str) -> CaseSearchResult:
    """대본의 신원 어휘로 사례 유형을 앞세워 검색한다. 어휘표만 쓰므로 읽기와 나란히 돌릴 수 있다."""
    preferred = frozenset().union(
        *(IDENTITY_PREFERRED_TYPES.get(kind, frozenset()) for kind in identity_kinds(transcript))
    )
    return search_voice_cases(transcript, preferred_types=preferred)


def search_gated(transcript: str) -> CaseSearchResult:
    """신원 어휘 검색 뒤, 사례 첨부 관문 점수가 VOICE_GATE_MIN 미만이면 사례를 붙이지 않는다.

    관문은 붙일지만 정한다(판정·결론·근거는 그대로). 붙을 사례가 없으면 관문을 돌리지 않는다.
    관문 예외는 그대로 올린다. _search 가 사례 미첨부 + similar_cases unchecked(failed)로 처리한다.
    """
    found = search_by_identity(transcript)
    if found.matches and attach_score(transcript) < search.VOICE_GATE_MIN:
        return found.model_copy(update={"matches": []})
    return found


def warm_up_voice_case_search() -> CaseSearchResult:
    """통화 사례 색인과 관문 모델을 기동 때 미리 읽는다(색인 콜드 적재 약 1.4초, 관문 약 30ms).

    색인을 먼저 채운다. 관문 모델을 읽지 못해도 색인 워밍업은 끝나 있고, 관문 실패는 요청 때
    사례 미첨부 + unchecked 로 처리된다.
    """
    result = search.warm_up_voice_case_search()
    try:
        load_model()
    except Exception as exc:
        LOGGER.warning("voice gate warm-up failed: %s", type(exc).__name__)
    return result


@dataclass
class VoiceRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    read_claims: Callable[[ReadingSource], Awaitable[ClaimReadingResult]] = read_claims
    # 동기 함수라 asyncio.to_thread 로 부른다
    search_cases: Callable[[str], CaseSearchResult] = search_gated
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
) -> tuple[list[NumberCheck], bool]:
    """받아들인 기관 주장이 KB 에 있고 상대 발화에 번호가 있으면 번호마다 대조한다."""
    if verify.speaker_split_failed or verify.other_speaker is None:
        # 누구의 말인지 몰라 상대가 말한 번호라고 할 수 없다
        return [], False
    org = None
    for ident in verify.claims.identity:
        if ident.listed_institution:
            org = resolve_org(ident.name) or resolve_org(ident.quote)
            if org:
                break
    if org is None:
        return [], False
    other_text = " ".join(
        seg.text for seg in source.segments if seg.side == Side.OTHER
    )
    checks: list[NumberCheck] = []
    failed = False
    for number in find_numbers(other_text)[:MAX_NUMBER_CHECKS]:
        try:
            checks.append(runtime.check_phone_number(number, org))
        except Exception as exc:
            LOGGER.warning("phone number check failed: %s", type(exc).__name__)
            failed = True
    return checks, failed


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
    """STT·화자 분리 결과를 분석한다. 질문으로 멈추지 않고 늘 결과를 낸다.

    예기치 못한 오류가 나도 예외 대신 분석 불가 결과를 돌려준다(CancelledError 만 올린다).
    """
    try:
        return await _analyze(transcript, runtime, time_budget_s)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.warning("voice analysis failed: %s", type(exc).__name__)
        return VoiceCaseResult(
            scenario=Scenario.VOICE_PHISHING,
            conclusion=Conclusion.NOT_ANALYZABLE,
            unchecked=[
                Unchecked(
                    item=UncheckedItem.TRANSCRIPT, reason=UncheckedReason.FAILED
                )
            ],
            failures=[FailureCode.MISSING_RESULT],
        )


def _not_analyzed(failures: list[FailureCode]) -> VoiceCaseResult:
    """대본은 있으나 처리 단계가 실패해 읽지 못한 결과."""
    result = decide_call(
        transcript_empty=False,
        verify=None,
        number_checks=[],
        similar=None,
        failures=[*failures, FailureCode.MISSING_RESULT],
    )
    _set_unchecked(result, UncheckedItem.TRANSCRIPT, UncheckedReason.FAILED)
    return result


async def _analyze(
    transcript: TranscriptResult,
    runtime: VoiceRuntime | None,
    time_budget_s: float,
) -> VoiceCaseResult:
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

    try:
        masked = mask_transcript(transcript)
        source = ReadingSource(
            kind=InputKind.AUDIO,
            segments=masked.segments,
            speaker_split_failed=transcript.speaker_split_failed,
        )
    except Exception as exc:
        # 가리지 못한 대본은 읽기·검색에 넘기지 않는다
        LOGGER.warning("transcript masking failed: %s", type(exc).__name__)
        return _not_analyzed(failures)

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
    try:
        verify = verify_call(
            source,
            reading.reading,
            derived_by="lexicon" if reading.status == "fallback" else "llm",
        )
    except Exception as exc:
        LOGGER.warning("claims verification failed: %s", type(exc).__name__)
        result = _not_analyzed(failures)
        if search_miss is not None:
            _set_unchecked(result, UncheckedItem.SIMILAR_CASES, search_miss)
        return result

    number_timeout = remaining() <= 0
    number_checks: list[NumberCheck] = []
    number_failed = False
    if not number_timeout:
        try:
            _fill_sides(source, verify)
            number_checks, number_failed = _check_numbers(runtime, source, verify)
        except Exception as exc:
            LOGGER.warning("number step failed: %s", type(exc).__name__)
            number_checks, number_failed = [], True

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
    elif number_failed and not any(
        c.result in ("official", "not_official") for c in number_checks
    ):
        _set_unchecked(result, UncheckedItem.PHONE_NUMBER, UncheckedReason.FAILED)
    return result
