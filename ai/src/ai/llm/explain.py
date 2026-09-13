"""판정 결과 → 사람이 읽을 문장.

LLM은 문장을 다듬는 용도로만 붙입니다. 실패하면 템플릿 그대로 나갑니다.
판정 자체는 절대 여기서 하지 않습니다.
"""

from ai.types import ReasonCode, Verdict

TEMPLATES: dict[ReasonCode, str] = {
    ReasonCode.NO_URL: "문자에서 링크를 찾지 못했습니다.",
    ReasonCode.OFFICIAL_MATCH: "{carrier}의 공식 주소가 맞습니다.",
    ReasonCode.LOOKALIKE: "{carrier} 공식 주소와 비슷하지만 다른 주소입니다.",
    ReasonCode.NOT_IN_WHITELIST: "확인된 택배사 공식 주소 목록에 없는 주소입니다.",
    ReasonCode.UNRESOLVED: "주소를 확인하지 못했습니다.",
}


def generate_explanation(verdict: Verdict) -> str:
    """항상 문자열을 반환합니다. 예외를 던지지 않습니다."""
    template = TEMPLATES[verdict.reason_code]
    return template.format(carrier=verdict.carrier_name or "택배사")
