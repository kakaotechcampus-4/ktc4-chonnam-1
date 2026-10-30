"""녹음 → TranscriptResult (스펙 D1). 받아쓴 글을 문장으로 자른다.

화자는 나누지 않는다(ADR 0007). 판정은 하지 않는다. 받아쓰기가 실패해도 예외를 던지지 않고
결과 모양으로 돌려준다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from ai.case.privacy import mask_pii
from ai.case.types import Segment, Side, TranscriptResult
from ai.llm import transcribe as transcribe_llm
from ai.types import FailureCode

LOGGER = logging.getLogger(__name__)
STT_TIMEOUT_SECONDS = 30.0
# 같은 문장이 이만큼보다 많이 연달아 나오면 whisper 반복 환각으로 본다
MAX_REPEAT = 5
# 마침표·물음표·느낌표·말줄임표 뒤에서 자른다. 물음표는 되묻기를 가리는 단서라 남긴다(forms)
# 숫자 사이의 점("010.1234.5678")은 자르지 않는다. 잘리면 가리기 규칙이 못 잡은 채 LLM 에 간다
SENTENCE_RE = re.compile(r"(?:[^.?!…]|(?<=\d)\.(?=\d))+[.?!…]*")


async def _default_transcribe(audio: bytes, filename: str) -> str:
    return await transcribe_llm.transcribe_audio(audio, filename)


@dataclass
class TranscribeRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    transcribe: Callable[[bytes, str], Awaitable[str]] = _default_transcribe


def split_sentences(text: str) -> list[str]:
    """문장으로 자르고, 같은 문장이 5번 넘게 연달아 나오면 한 번만 남긴다."""
    rows = [s.strip() for s in SENTENCE_RE.findall(text) if s.strip()]
    out: list[str] = []
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j] == rows[i]:
            j += 1
        out.extend([rows[i]] if j - i > MAX_REPEAT else rows[i:j])
        i = j
    return out


def _wav_seconds(audio: bytes) -> float | None:
    """wav 만 길이를 안다. 다른 형식은 변환 없이 모른다고 둔다."""
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return round(w.getnframes() / w.getframerate(), 1)
    except Exception:  # 깨진 헤더(struct.error 등)여도 길이 계산 때문에 분석이 멈추면 안 된다
        return None


def _failure_code(exc: BaseException) -> FailureCode:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return FailureCode.TIMEOUT
    if isinstance(exc, ValueError):
        return FailureCode.INVALID_OUTPUT
    return FailureCode.LLM_ERROR


async def transcribe_call(
    audio: bytes,
    filename: str,
    *,
    runtime: TranscribeRuntime | None = None,
) -> TranscriptResult:
    rt = runtime or TranscribeRuntime()
    meta = {
        "stt_model": transcribe_llm.resolve_model(),
        "audio_seconds": _wav_seconds(audio),
    }

    try:
        text = await asyncio.wait_for(
            rt.transcribe(audio, filename), timeout=STT_TIMEOUT_SECONDS
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.warning("stt failed: %s", type(exc).__name__)
        return TranscriptResult(segments=[], failure=_failure_code(exc), **meta)

    # LLM 에 보내기 전에 가린다(스펙 D2). 세그먼트 텍스트도 가린 문장을 쓴다
    sentences = [mask_pii(s) for s in split_sentences(mask_pii(text))]
    return TranscriptResult(
        segments=[Segment(side=Side.UNKNOWN, text=s) for s in sentences], **meta
    )
