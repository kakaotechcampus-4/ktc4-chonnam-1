"""통화 대본·링크 없는 문자에서 상대의 주장·요구를 읽는다 (v3 §5).

LLM 은 읽기만 한다. 위험·안전 판단은 없고, 누가 말했는지도 고르지 않는다. 인용이 실제 원문에
있는지, 받는 사람 쪽 말 꼴이 아닌지는 verify 가 확인한다. 실패하면 어휘표로 같은 모양의 결과를 돌려준다.
"""

import logging
import os
from pathlib import Path

from openai import AsyncOpenAI

from ai.case.lexicon import read_with_lexicon
from ai.case.types import ClaimReading, ClaimReadingResult, InputKind, ReadingSource
from ai.llm._client import create_client, data_line, failure_of, parse_answer, prompt_text, required_env
from ai.types import FailureCode


LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 12.0
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "v1"
PROMPT_PATH = _PROMPTS_DIR / "claims_call.md"
TEXT_PROMPT_PATH = _PROMPTS_DIR / "claims_text.md"
# 입력 종류별 프롬프트. 사진(image)은 아직 읽지 않는다
_PROMPTS = {InputKind.AUDIO: PROMPT_PATH, InputKind.TEXT: TEXT_PROMPT_PATH}


def _resolve_model(model: str | None) -> str:
    # 인자 → CLAIMS_MODEL → LLM_MODEL (스펙 D3)
    return model or os.getenv("CLAIMS_MODEL", "").strip() or required_env("LLM_MODEL")


def _render_segments(source: ReadingSource) -> str:
    """세그먼트(통화는 문장, 문자는 줄)를 한 줄씩 data 태그로 감싼다. 화자 표기는 없다(ADR 0008).
    원문 안의 닫는 태그는 무력화한다."""
    return "\n".join(data_line(seg.text) for seg in source.segments)


def _fallback(source: ReadingSource, failure: FailureCode) -> ClaimReadingResult:
    return read_with_lexicon(source).model_copy(update={"failure": failure})


async def read_claims(
    source: ReadingSource,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    timeout_s: float = TIMEOUT_SECONDS,
) -> ClaimReadingResult:
    prompt_path = _PROMPTS.get(source.kind)
    if prompt_path is None:
        raise ValueError("read_claims 는 통화(audio)와 문자(text)만 지원합니다")

    try:
        reading = await parse_answer(
            ClaimReading,
            system=prompt_text(prompt_path),
            user=_render_segments(source),
            client=client,
            make_client=create_client,
            model=_resolve_model(model),
            timeout_s=timeout_s,
        )
        # 읽을 주장이 없는 정상 통화는 빈 결과가 맞다. 실패로 보지 않는다 (R14)
        return ClaimReadingResult(status="ok", reading=reading)
    except Exception as exc:  # noqa: BLE001 — LLM 이 실패해도 폴백 결과를 낸다 (원칙 3). 실패 코드는 failure_of 가 정한다
        return _fallback(source, failure_of(exc, "claims reading", LOGGER))
