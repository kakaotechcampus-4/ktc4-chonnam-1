"""위험 신호 제안.

LLM은 후보만 냅니다. 최종 판정은 ai.verdict.decide()가 합니다.
스크린샷은 격리 서버가 이미 멀티모달로 봤으므로 다시 보내지 않습니다.
"""

import asyncio
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from ai.llm._client import create_client, required_env
from ai.types import (
    MessageAnalysis,
    Observations,
    RiskSignal,
    SignalProposal,
)

LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 2.0
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "signals.md"


def _observation_digest(observations: Observations | None) -> dict:
    """판정에 쓰지 않는 필드는 프롬프트에서 뺍니다."""
    if observations is None:
        return {}
    return {
        "page_state": observations.page_state.value,
        "payload_type": observations.payload_type,
        "checks": {
            key: {"state": check.state.value, "items": check.items}
            for key, check in observations.checks.items()
        },
        "static_risk_signals": observations.static_risk_signals,
        "unchecked": [
            {"check": item.check, "reason": item.reason}
            for item in observations.unchecked
        ],
    }


async def extract_signals(
    masked_text: str,
    extracted: MessageAnalysis,
    observations: Observations | None,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> list[RiskSignal]:
    """실패하면 빈 목록을 반환합니다. 예외를 던지지 않습니다."""
    digest = _observation_digest(observations)
    if not masked_text.strip() and not digest:
        return []

    user_content = json.dumps(
        {
            "message": masked_text,
            "extracted": extracted.model_dump(mode="json"),
            "observations": digest,
        },
        ensure_ascii=False,
    )

    try:
        llm = client or create_client(TIMEOUT_SECONDS)
        model_name = model or required_env("LLM_MODEL")
        response = await asyncio.wait_for(
            llm.chat.completions.parse(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": PROMPT_PATH.read_text(encoding="utf-8"),
                    },
                    {"role": "user", "content": user_content},
                ],
                response_format=SignalProposal,
                temperature=0,
            ),
            timeout=TIMEOUT_SECONDS,
        )
        message = response.choices[0].message
        if message.refusal or message.parsed is None:
            return []
        return list(message.parsed.signals)
    except Exception as exc:
        LOGGER.warning("signal extraction failed: %s", type(exc).__name__)
        return []
