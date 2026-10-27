"""통화 대본에서 상대의 주장·요구를 읽는다 (v3 §5, 통화 모드만).

LLM 은 읽기만 한다. 위험·안전 판단은 없고, 인용이 실제 원문에 있는지는
verify 가 확인한다. 실패하면 어휘표로 같은 모양의 결과를 돌려준다.
"""

import asyncio
import logging
import os
from pathlib import Path

from openai import APIResponseValidationError, APITimeoutError, AsyncOpenAI, LengthFinishReasonError
from pydantic import ValidationError

from ai.case.lexicon import read_with_lexicon
from ai.case.types import ClaimReading, ClaimReadingResult, InputKind, ReadingSource
from ai.llm._client import create_client, required_env
from ai.types import FailureCode


LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 12.0
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "claims_call.md"


def _resolve_model(model: str | None) -> str:
    # 인자 → CLAIMS_MODEL → LLM_MODEL (스펙 D3)
    return model or os.getenv("CLAIMS_MODEL", "").strip() or required_env("LLM_MODEL")


def _render_segments(source: ReadingSource) -> str:
    """세그먼트를 한 줄씩 data 태그로 감싼다. 원문 안의 닫는 태그는 무력화한다."""
    lines = []
    for seg in source.segments:
        speaker = (seg.speaker or "알 수 없음").replace('"', "'")
        text = " ".join(seg.text.split()).replace("</data", "< /data")
        lines.append(f'<data speaker="{speaker}">{text}</data>')
    return "\n".join(lines)


def _fallback(source: ReadingSource, failure: FailureCode) -> ClaimReadingResult:
    return read_with_lexicon(source).model_copy(update={"failure": failure})


async def read_claims(
    source: ReadingSource,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    timeout_s: float = TIMEOUT_SECONDS,
) -> ClaimReadingResult:
    if source.kind != InputKind.AUDIO:
        raise ValueError("read_claims 는 통화(audio)만 지원합니다")

    try:
        llm = client or create_client(timeout_s)
        response = await asyncio.wait_for(
            llm.chat.completions.parse(
                model=_resolve_model(model),
                messages=[
                    {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                    {"role": "user", "content": _render_segments(source)},
                ],
                response_format=ClaimReading,
                temperature=0,
            ),
            timeout=timeout_s,
        )
        message = response.choices[0].message
        if message.refusal:
            return _fallback(source, FailureCode.REFUSED)
        if message.parsed is None:
            return _fallback(source, FailureCode.INVALID_OUTPUT)
        reading = ClaimReading.model_validate(message.parsed)
        # 읽을 주장이 없는 정상 통화는 빈 결과가 맞다. 실패로 보지 않는다 (R14)
        return ClaimReadingResult(status="ok", reading=reading)
    except asyncio.CancelledError:
        raise
    except (TimeoutError, APITimeoutError) as exc:
        LOGGER.warning("claims reading failed: %s", type(exc).__name__)
        return _fallback(source, FailureCode.TIMEOUT)
    except (ValidationError, APIResponseValidationError, LengthFinishReasonError) as exc:
        LOGGER.warning("claims reading failed: %s", type(exc).__name__)
        return _fallback(source, FailureCode.INVALID_OUTPUT)
    except Exception as exc:
        LOGGER.warning("claims reading failed: %s", type(exc).__name__)
        return _fallback(source, FailureCode.LLM_ERROR)
