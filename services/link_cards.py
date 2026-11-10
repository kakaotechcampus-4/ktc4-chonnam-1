"""링크 문자 분석 결과(`AnalysisResponse` dict) 카드: R1·R2·R8·R9.

`select_result_card()` 로 카드를 고르고 `render_card()` 로 값을 채운 뒤 "자세히 보기" 버튼에 job_id 를 꽂는다.
신호 상세는 `detail_carousel.py` 가 따로 만든다. 카카오 블록 흐름에서 "자세히 보기"를 누르면 다른 블록이
캐러셀을 보여 주므로, 결과 카드와 캐러셀은 서로 다른 응답이다.
"""

from services.chatbot_copy_data import (
    get_message_failure_copy,
)
from services.official_domain_service import brand_for_domain, official_homepage
from services.kakao_card_renderer import render_card, substitute_values
from services.result_card_selector import select_result_card
from services.card_parts import (
    NO_LINES,
    NO_RISK,
    PARTIAL,
    TEXT_CARD_MAX_LENGTH,
    UNKNOWN,
    address_lines,
    env_lines,
    inject_job_id,
    shorten_evidence,
)

_MAX_REQUESTED_ACTIONS = 3
_R9_DEFAULT_MESSAGE_REASON = "문자 분석 결과를 전달받지 못했습니다."


def _r9_org_label(brand: str | None) -> str:
    if brand and brand != UNKNOWN:
        return brand
    return "보낸 기관의"


def _r9_sender_lines(brand: str | None) -> tuple[str | None, str]:
    """보낸 곳은 항상 '확인하지 못한 것'에 들어간다 (PR #39 멘토 리뷰:
    "검찰청이라고 주장한다"와 "검찰청이 보냈다"를 구분하라는 지적)."""
    if brand and brand != UNKNOWN:
        return f"문자에 적힌 보낸 곳: {brand}", "실제로 보낸 곳"
    return None, "실제로 보낸 곳"


def _r9_requested_actions_line(doubts: list[dict]) -> str | None:
    values = [
        doubt.get("value") for doubt in doubts
        if doubt.get("value") and doubt.get("value") != UNKNOWN
    ]
    if not values:
        return None
    joined = ", ".join(values[:_MAX_REQUESTED_ACTIONS])
    return f"문자에서 요구한 것: {joined}"


def _r9_message_lines(
    answer: str | None, failures: list[str]
) -> tuple[str | None, str | None]:
    """이슈 #59: `partial`/`failed`는 answer 이름이 아니라 AI가 보존한
    실제 실패 원인(`failures[0]`)으로 설명한다 — "분석을 끝내지
    못했어요" 같은 뭉뚱그린 문구 대신 "시간 초과라 못 봤다"처럼
    구체적으로 말한다. 원인 정보가 없는 기존 응답도 기본 안내로
    떨어지니 깨지지 않는다(`get_message_failure_copy`)."""

    if answer == NO_RISK:
        return "문자에서 위험 신호를 찾지 못했어요", None

    if answer in (PARTIAL, "failed"):
        return None, f"문자 내용 ({get_message_failure_copy(failures)})"

    if answer == "not_run":
        return None, "문자 내용"

    return None, None


def _r9_blank_values(analysis_result: dict) -> dict[str, str]:
    """r9-uncertain의 `{{ org_label }}`, `{{ checked }}`, `{{ unchecked }}`를 채운다.

    docs/designs/chatbot-copy.md "R9 판단 보류 → 빈칸 채우는 규칙"의 표를
    그대로 따른다. 행 순서: 보낸 곳 → 요구 행동 → 주소 → 문자 → 페이지.
    """

    url = analysis_result.get("url") or {}
    message = analysis_result.get("message") or {}
    env = analysis_result.get("env") or {}
    brand = message.get("brand")

    sender_checked, sender_unchecked = _r9_sender_lines(brand)
    requested_checked = _r9_requested_actions_line(
        (message.get("details") or {}).get("doubts") or []
    )
    address_checked, address_unchecked = address_lines(url.get("official"), brand)
    message_checked, message_unchecked = _r9_message_lines(
        message.get("answer"),
        ((message.get("details") or {}).get("reason") or {}).get("failures") or [],
    )
    env_checked, env_unchecked = env_lines(env.get("answer"))

    checked = [
        line for line in (
            sender_checked, requested_checked, address_checked,
            message_checked, env_checked,
        )
        if line
    ]
    unchecked = [
        line for line in (
            sender_unchecked, address_unchecked, message_unchecked, env_unchecked,
        )
        if line
    ]

    return {
        "org_label": _r9_org_label(brand),
        "checked": "\n".join(f"- {line}" for line in checked) or NO_LINES,
        "unchecked": "\n".join(f"- {line}" for line in unchecked) or NO_LINES,
    }


def _r9_message_reason(message: dict) -> str:
    """이슈 #68: `message.brand`는 택배사만 알아보므로, 청소행정과처럼
    택배사가 아닌 기관이 내세운 명분·요구는 이 칸으로만 전달된다.
    확인한 사실이 아니라 "문자가 주장한 내용"이라 기본값도 '확인'이
    아니라 '전달받지 못했습니다'로 둔다."""

    reason = ((message.get("details") or {}).get("reason") or {}).get("text")
    reason = (reason or "").strip()
    return reason or _R9_DEFAULT_MESSAGE_REASON


def _result_card_values(analysis_result: dict, card_name: str) -> dict[str, str]:
    """카드별로 필요한 `{{ placeholder }}` 값을 조립한다.

    org_name은 AI가 식별한 `message.brand`를 그대로 쓴다 — `Brand` enum의
    값 자체가 한글 표준명(예: "CJ대한통운")이라 별도 표시명 매핑이
    필요 없다 (`ai/src/ai/types.py`).
    """

    url = analysis_result.get("url") or {}
    message = analysis_result.get("message") or {}
    brand = message.get("brand")
    if card_name == "r2-official" and (not brand or brand == UNKNOWN):
        # #164: 문자에 기관 이름이 없으면 공식 대조에 쓴 주소의 기관을 쓴다
        brand = brand_for_domain(url.get("domain"))

    values: dict[str, str] = {}

    if brand:
        values["org_name"] = brand

    if card_name == "r2-official":
        # 버튼 주소는 화이트리스트에서 만든다. 문자 속 링크를 그대로 쓰지 않는다.
        homepage = official_homepage(brand, url.get("domain"))
        if homepage:
            values["official_url"] = homepage

    if card_name == "r9-uncertain":
        values.update(_r9_blank_values(analysis_result))

    return values


def render_result_card(
    analysis_result: dict,
    job_id: str | None = None,
    *,
    allow_details: bool = True,
) -> dict:
    """분석 결과에 맞는 결과 카드를 고르고 완성된 Kakao 응답을 돌려준다."""

    card_name = select_result_card(analysis_result)
    values = _result_card_values(analysis_result, card_name)

    card = render_card(card_name, values)

    if card_name == "r9-uncertain":
        message = analysis_result.get("message") or {}
        reason = _r9_message_reason(message)
        node = card["template"]["outputs"][0]["textCard"]
        # 다른 빈칸을 먼저 채운 뒤 400자에서 남는 만큼만 message_reason에 쓴다.
        fixed = substitute_values(node["description"], {"message_reason": ""})
        available = TEXT_CARD_MAX_LENGTH - len(fixed)
        node["description"] = substitute_values(
            node["description"],
            {"message_reason": shorten_evidence(reason, available)},
        )

    return inject_job_id(card, job_id, allow_details=allow_details)
