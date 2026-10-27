"""녹음 → TranscriptResult (스펙 D1). 받아쓰기와 화자 분리를 묶는다.

판정은 하지 않는다. 어느 단계가 실패해도 예외를 던지지 않고 결과 모양으로 돌려준다.
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
from ai.llm import diarize as diarize_llm
from ai.llm import transcribe as transcribe_llm
from ai.types import FailureCode

LOGGER = logging.getLogger(__name__)
STT_TIMEOUT_SECONDS = 30.0
DIARIZE_TIMEOUT_SECONDS = 20.0
UNKNOWN_SPEAKER = "기타"
# 같은 문장이 이만큼보다 많이 연달아 나오면 whisper 반복 환각으로 본다
MAX_REPEAT = 5
# 기타·누락이 문장의 이 비율을 넘으면 화자 분리를 믿지 않는다
MAX_UNKNOWN_RATIO = 0.3
# 마침표·물음표·느낌표·말줄임표 뒤에서 자른다. whisper 는 짧은 맞장구에도 마침표를 찍어 화자가 바뀌는 곳에서 잘 끊긴다
SENTENCE_RE = re.compile(r"[^.?!…]+[.?!…]*")


async def _default_transcribe(audio: bytes, filename: str) -> str:
    return await transcribe_llm.transcribe_audio(audio, filename)


async def _default_diarize(sentences: list[str]) -> dict[int, str]:
    return await diarize_llm.pick_speakers(sentences)


@dataclass
class TranscribeRuntime:
    """바깥 의존을 바꿔 끼우는 자리. 테스트는 가짜로 바꾼다."""

    transcribe: Callable[[bytes, str], Awaitable[str]] = _default_transcribe
    diarize: Callable[[list[str]], Awaitable[dict[int, str]]] = _default_diarize


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
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def _failure_code(exc: BaseException) -> FailureCode:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return FailureCode.TIMEOUT
    if isinstance(exc, ValueError):
        return FailureCode.INVALID_OUTPUT
    return FailureCode.LLM_ERROR


def _merge(sentences: list[str], speakers: list[str]) -> list[Segment]:
    """같은 화자가 이어 말한 문장은 한 세그먼트로 합친다."""
    segments: list[Segment] = []
    for text, speaker in zip(sentences, speakers):
        if segments and segments[-1].speaker == speaker:
            segments[-1].text += " " + text
        else:
            segments.append(Segment(side=Side.UNKNOWN, speaker=speaker, text=text))
    return segments


def _split_untrustworthy(speakers: list[str]) -> bool:
    if "화자1" not in speakers or "화자2" not in speakers:
        return True
    unknown = sum(1 for s in speakers if s == UNKNOWN_SPEAKER)
    return unknown / len(speakers) > MAX_UNKNOWN_RATIO


async def transcribe_call(
    audio: bytes,
    filename: str,
    *,
    runtime: TranscribeRuntime | None = None,
) -> TranscriptResult:
    rt = runtime or TranscribeRuntime()
    meta = {
        "stt_model": transcribe_llm.resolve_model(),
        "diarize_model": diarize_llm.resolve_model(),
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
        return TranscriptResult(
            segments=[], speaker_split_failed=False, failure=_failure_code(exc), **meta
        )

    # LLM 에 보내기 전에 가린다(스펙 D2). 세그먼트 텍스트도 가린 문장을 쓴다
    sentences = [mask_pii(s) for s in split_sentences(text)]
    if not sentences:
        return TranscriptResult(segments=[], speaker_split_failed=False, **meta)

    try:
        labels = await asyncio.wait_for(
            rt.diarize(sentences), timeout=DIARIZE_TIMEOUT_SECONDS
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # 대본은 살린다. 분리 실패는 분석 쪽(U-4)이 다룬다
        LOGGER.warning("diarize failed: %s", type(exc).__name__)
        return TranscriptResult(
            segments=_merge(sentences, [UNKNOWN_SPEAKER] * len(sentences)),
            speaker_split_failed=True,
            **meta,
        )

    speakers = [labels.get(i, UNKNOWN_SPEAKER) for i in range(len(sentences))]
    return TranscriptResult(
        segments=_merge(sentences, speakers),
        speaker_split_failed=_split_untrustworthy(speakers),
        **meta,
    )
