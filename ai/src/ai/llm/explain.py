"""판정 결과 → 사람이 읽을 문장.

LLM은 문장을 다듬는 용도로만 붙입니다. 실패하면 템플릿 그대로 나갑니다.
판정 자체는 절대 여기서 하지 않습니다.
"""

import asyncio
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from ai.llm.analyze import _create_client, _required_env
from ai.types import (
    CaseSearchResult,
    FinalState,
    Observations,
    ReasonCode,
    Verdict,
)

LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 2.0
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "compare.md"

TEMPLATES: dict[ReasonCode, str] = {
    ReasonCode.NO_URL: "문자에서 링크를 찾지 못했습니다.",
    ReasonCode.OFFICIAL_MATCH: "{carrier}의 공식 주소가 맞습니다.",
    ReasonCode.LOOKALIKE: "{carrier} 공식 주소와 비슷하지만 다른 주소입니다.",
    ReasonCode.NOT_IN_WHITELIST: "확인된 택배사 공식 주소 목록에 없는 주소입니다.",
    ReasonCode.OFFICIAL_BUT_RISKY: (
        "{carrier}의 공식 주소가 맞지만 페이지에서 위험 신호가 확인됐습니다."
    ),
    ReasonCode.UNRESOLVED: "주소를 확인하지 못했습니다.",
}

STATE_TEMPLATES: dict[FinalState, str] = {
    FinalState.SMISHING_SUSPECTED: "스미싱이 의심됩니다. 링크를 열지 마세요.",
    FinalState.OFFICIAL_DOMAIN: (
        "등록된 공식 주소와 일치합니다. 다만 문자 전체의 안전을 보증하지는 않습니다."
    ),
    FinalState.INCONCLUSIVE: "판단에 필요한 근거가 부족합니다. 링크를 열지 마세요.",
    FinalState.NOT_ANALYZABLE: "분석에 필요한 확인을 하지 못했습니다.",
    FinalState.INPUT_REQUIRED: "분석할 링크가 포함된 문자를 보내주세요.",
}


def generate_explanation(verdict: Verdict) -> str:
    """항상 문자열을 반환합니다. 예외를 던지지 않습니다."""
    reason = TEMPLATES[verdict.reason_code].format(
        carrier=verdict.carrier_name or "택배사"
    )
    if verdict.final_state is None:
        return reason
    return f"{STATE_TEMPLATES[verdict.final_state]} {reason}"


def _context(
    verdict: Verdict,
    observations: Observations | None,
    cases: CaseSearchResult | None,
) -> str:
    return json.dumps(
        {
            "final_state": verdict.final_state.value if verdict.final_state else None,
            "reason_code": verdict.reason_code.value,
            "carrier_name": verdict.carrier_name,
            "accepted_signals": [
                {"code": item.code.value, "evidence_ref": item.evidence_ref}
                for item in verdict.accepted_signals
            ],
            "observations": (
                {
                    "page_state": observations.page_state.value,
                    "checks": {
                        key: {"state": check.state.value, "items": check.items}
                        for key, check in observations.checks.items()
                    },
                    "unchecked": [
                        {"check": item.check, "reason": item.reason}
                        for item in observations.unchecked
                    ],
                }
                if observations
                else None
            ),
            "cases": (
                {
                    "status": cases.status.value,
                    "matches": [
                        {"case_id": item.case_id, "similarity": item.similarity}
                        for item in cases.matches
                    ],
                }
                if cases
                else None
            ),
        },
        ensure_ascii=False,
    )


async def explain(
    verdict: Verdict,
    observations: Observations | None = None,
    cases: CaseSearchResult | None = None,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> str:
    """LLM 문장을 만들고, 실패하면 템플릿으로 폴백합니다."""
    fallback = generate_explanation(verdict)

    try:
        llm = client or _create_client()
        model_name = model or _required_env("LLM_MODEL")
        response = await asyncio.wait_for(
            llm.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": PROMPT_PATH.read_text(encoding="utf-8"),
                    },
                    {"role": "user", "content": _context(verdict, observations, cases)},
                ],
                temperature=0,
            ),
            timeout=TIMEOUT_SECONDS,
        )
        message = response.choices[0].message
        if message.refusal or not (message.content or "").strip():
            return fallback
        return message.content.strip()
    except Exception as exc:
        LOGGER.warning("explanation generation failed: %s", type(exc).__name__)
        return fallback
