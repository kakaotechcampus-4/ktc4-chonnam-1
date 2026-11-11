"""링크 문자 결과의 "자세히 보기" 캐러셀(C1~C4)."""

import logging
import re
from typing import Any

from ai.kb.guidance import find_guidance
from services.chatbot_copy_data import (
    GUIDANCE_CARD_CONTACT,
    GUIDANCE_DELIVERY_ACTION,
    GUIDANCE_DELIVERY_ALL_CONTACTS,
    GUIDANCE_DELIVERY_CONTACTS,
    get_message_failure_copy,
    get_signal_copy,
    get_unverified_copy,
)
from services.kakao_card_renderer import render_card, substitute_values
from services.link_cards import r9_blank_values
from services.message_cards import link_caution_lines, message_claim_lines
from services.result_card_selector import select_result_card
from services.card_parts import (
    CAROUSEL_TEXT_MAX_LENGTH,
    EVIDENCE_MAX_LENGTH,
    NO_LINES,
    NO_RISK,
    fit_lines,
    shorten_evidence,
)

_LOGGER = logging.getLogger(__name__)
MAX_CAROUSEL_SIGNAL_ITEMS = 8  # + C2 + C3 = 10장 (카카오 캐러셀 상한)
MAX_CAROUSEL_TOTAL_ITEMS = 10  # 카카오 캐러셀 자체 상한
_FIXED_CAROUSEL_ITEMS = 2  # C2(미확인) + C3(행동 안내)는 항상 1장씩 나간다
_MAX_GUIDANCE_ITEMS = 3  # find_guidance() 계약(최대 3개)과 별개로 BE에서도 한 번 더 막는다
_GUIDANCE_TEXT_MAX_LENGTH = 128  # FE 합의: 카드 1장당 128자
_COMPLETE_ANSWERS = (NO_RISK, "risk_found")
_R9_DEFAULT_MESSAGE_REASON = "문자 분석 결과를 전달받지 못했습니다."


def _message_unverified_line(message: dict) -> str | None:
    """message가 완료 상태(no_risk_found/risk_found)가 아니면 그 이유를
    "문자: ..." 줄로 돌려준다. 완료됐으면 보여줄 "확인 못 한 것"이 없으니
    None이다 (이슈 #59: 문자 분석 실패와 페이지 분석 실패를 구분하고,
    정상 완료된 페이지 분석을 문자 분석 실패 때문에 실패로 보이게 하지
    않는다)."""

    answer = message.get("answer")
    if answer in _COMPLETE_ANSWERS:
        return None
    failures = ((message.get("details") or {}).get("reason") or {}).get("failures") or []
    return f"문자: {get_message_failure_copy(failures)}"


def _env_unverified_line(env: dict) -> str | None:
    answer = env.get("answer")
    if answer in _COMPLETE_ANSWERS:
        return None
    return f"페이지: {get_unverified_copy(answer)}"


def _guidance_description(guidance: Any, brand: str | None = None) -> str:
    """이슈 #69: `kind`에 따라 FE가 정한 두 형식 중 하나로 문장을 만든다.

    `procedure`는 "{source}는 ~라고 안내했어요({연도})" 형식이다 — 문자가
    가짜라는 결론이 아니라 공식 기관이 실제로 안내한 절차임을 보여주는
    것이 목적이라, 여기서 "그래서 가짜/진짜"라는 판단을 덧붙이지 않는다.
    긴 연락처 규칙은 FE가 정한 짧은 문구를 사용한다. 출처 표기가 넘치는
    NR-0012·0013은 출처·연도와 원문을 두 줄로 배치한다. 그 밖의 긴 자료는
    기존 길이 제한을 유지하되 전화번호·URL 중간에서 자르지 않는다.
    """

    kind = getattr(guidance, "kind", None)
    text = getattr(guidance, "text", None) or ""
    rule_id = getattr(guidance, "rule_id", None)

    if kind == "verification" and len(text) > _GUIDANCE_TEXT_MAX_LENGTH:
        if rule_id == "NR-0015":
            contact = GUIDANCE_DELIVERY_CONTACTS.get(brand)
            contact_line = (
                f"{contact[0]} 고객센터 {contact[1]}" if contact
                else GUIDANCE_DELIVERY_ALL_CONTACTS
            )
            return f"{GUIDANCE_DELIVERY_ACTION}\n{contact_line}"
        if rule_id == "NR-0019":
            # 현재 분석 결과에는 카드사 식별 필드가 없으므로 추측하지 않는다.
            return GUIDANCE_CARD_CONTACT

    if kind == "procedure":
        source = getattr(guidance, "source", None) or "관련 기관"
        published_at = getattr(guidance, "published_at", None) or ""
        year = published_at[:4] if published_at else ""
        year_suffix = f"({year})" if year else ""
        original_text = text
        text = f"{source}는 {original_text}라고 안내했어요{year_suffix}."
        if len(text) > _GUIDANCE_TEXT_MAX_LENGTH and rule_id in ("NR-0012", "NR-0013"):
            text = f"{source}{year_suffix}\n{original_text}"

    if len(text) <= _GUIDANCE_TEXT_MAX_LENGTH:
        return text
    end = _GUIDANCE_TEXT_MAX_LENGTH - 1
    for token in re.finditer(r"https?://\S+|(?<!\d)\+?\d[\d-]*", text):
        if token.start() < end < token.end():
            end = token.start()
            break
    return text[:end].rstrip() + "…"


def _guidance_items(analysis_result: dict) -> list[dict]:
    """최종 분석 결과로 `find_guidance()`를 불러 캐러셀 항목을 만든다.

    이슈 계약상 `find_guidance()`는 예외를 던지지 않지만, 아직 merge되지
    않은 AI 쪽 코드를 믿고 그 계약에만 기대지 않는다 — 안내 카드 하나
    때문에 "자세히 보기" 전체가 깨지면 안 된다(CLAUDE.md 절대 원칙 3과
    같은 이유: 부가 기능 실패가 핵심 응답을 막으면 안 된다).
    """

    try:
        guidance_list = find_guidance(analysis_result) or []
    except Exception as e:  # noqa: BLE001 — 안내 카드 하나 때문에 "자세히 보기" 전체가 깨지면 안 된다 (원칙 3)
        _LOGGER.warning("[GUIDANCE ERROR] %s", type(e).__name__)
        return []

    if not guidance_list:
        return []

    template = render_card("c4-guidance")
    item_template = template["template"]["outputs"][0]["carousel"]["items"][0]

    return [
        substitute_values(item_template, {"guidance_text": _guidance_description(
            guidance, (analysis_result.get("message") or {}).get("brand"),
        )})
        for guidance in guidance_list[:_MAX_GUIDANCE_ITEMS]
    ]


def _r9_message_reason(message: dict) -> str:
    """이슈 #68: `message.brand`는 택배사만 알아보므로, 청소행정과처럼
    택배사가 아닌 기관이 내세운 명분·요구는 이 칸으로만 전달된다.
    확인한 사실이 아니라 "문자가 주장한 내용"이라 기본값도 '확인'이
    아니라 '전달받지 못했습니다'로 둔다."""

    reason = ((message.get("details") or {}).get("reason") or {}).get("text")
    reason = (reason or "").strip()
    return reason or _R9_DEFAULT_MESSAGE_REASON


def _r9_detail_items(analysis_result: dict, case_result: dict | None) -> list[dict]:
    """R9 '자세히 보기'에 더하는 장(D19, 4절 "판정 요약 카드에서 옮긴 내용").

    요약 카드 앞면에서 뺀 확인한 것과 확인하지 못한 것(이유 포함)을 장으로 둔다. 확인 범위(C2)는
    그대로 둔다. 실패 이유(#59)를 128자 C2 에 밀어 넣으면 이유가 잘려 나가기 때문이다.
    #89 주의 갈래면 그 카드가 쓴 주장, 확인한 것, 확인하지 못한 것을 쓴다. 아니면 #68 의
    '문자에 적힌 내용'을 장으로 둔다. 택배사가 아닌 기관의 명분은 이 칸으로만 전달된다.
    """

    templates = render_card("c-detail-carousel-message")["template"]["outputs"][0]["carousel"]["items"]
    lines_template, checked_template = templates[0], templates[1]

    def lines_item(title: str, lines: list[str]) -> dict:
        return substitute_values(lines_template, {
            "claims_title": title, "claim_lines": fit_lines(lines, CAROUSEL_TEXT_MAX_LENGTH),
        })

    items = []
    if case_result:
        checked_lines, unchecked_lines = link_caution_lines(analysis_result, case_result)
        claim_lines = message_claim_lines(case_result)
        if claim_lines:
            items.append(lines_item("⚠️ 주의할 점", claim_lines))
    else:
        blank = r9_blank_values(analysis_result)
        checked_lines = [line for line in blank["checked"].splitlines() if line != NO_LINES]
        unchecked_lines = [line for line in blank["unchecked"].splitlines() if line != NO_LINES]
        items.append(lines_item(
            "📩 문자에 적힌 내용", [_r9_message_reason(analysis_result.get("message") or {})],
        ))

    items.append(substitute_values(checked_template, {
        "checked": fit_lines(checked_lines or [NO_LINES], CAROUSEL_TEXT_MAX_LENGTH),
    }))
    items.append(lines_item("❌ 확인하지 못한 것", unchecked_lines or [NO_LINES]))
    return items


def render_detail_carousel(analysis_result: dict, case_result: dict | None = None) -> dict:
    """"자세히 보기"에서 쓸 의심 근거 캐러셀을 만든다.

    c-detail-carousel.json의 items[0]을 "의심 근거(C1)" 템플릿으로 보고
    message.details.signals 개수만큼 복제한다. items[1]은 "확인하지 못한
    부분(C2)", items[2]는 "행동 안내(C3)"로 고정 1장씩이다. 이슈 #69로
    추가된 공식 확인 방법 카드(`find_guidance()` 결과)는 맨 뒤에 붙인다.

    페이지(`env.details.signals`) 신호는 아직 고정 문구가 없어
    (chatbot-copy.md 4절) 이번에는 문자 신호만 카드로 만든다.

    전체 10장 상한(카카오 캐러셀 자체 제약)은 C2·C3 2장을 먼저 빼고
    남는 자리를 신호 카드와 안내 카드가 나눠 쓴다 — 안내 카드가 있으면
    신호 카드 상한(`MAX_CAROUSEL_SIGNAL_ITEMS`)이 그만큼 줄어든다.
    """

    template = render_card("c-detail-carousel")
    items_template = template["template"]["outputs"][0]["carousel"]["items"]
    c1_template, c2_template, c3_template = items_template

    message = analysis_result.get("message") or {}
    signals = (message.get("details") or {}).get("signals") or []

    env = analysis_result.get("env") or {}

    guidance_items = _guidance_items(analysis_result)

    # D19: R9(와 그 자리의 #89 주의 갈래) 요약 카드에서 뺀 내용
    r9_items = (
        _r9_detail_items(analysis_result, case_result)
        if case_result or select_result_card(analysis_result) == "r9-uncertain"
        else []
    )

    signal_budget = max(
        0,
        min(
            MAX_CAROUSEL_SIGNAL_ITEMS,
            MAX_CAROUSEL_TOTAL_ITEMS - _FIXED_CAROUSEL_ITEMS - len(guidance_items) - len(r9_items),
        ),
    )

    c1_items = []
    for signal in signals[:signal_budget]:
        code = signal.get("code", "")
        evidence = signal.get("evidence") or ""
        signal_title, reason = get_signal_copy(code)

        # Keep the complete reason; only shorten the quoted observation to fit textCard.
        fixed_description = substitute_values(c1_template, {
            "signal_title": signal_title,
            "observed": "문자에 ''라고 적혀 있어요.",
            "reason": reason,
        })["description"]
        evidence_budget = min(EVIDENCE_MAX_LENGTH, max(0, 128 - len(fixed_description)))
        observed = (
            f"문자에 '{shorten_evidence(evidence, evidence_budget)}'라고 적혀 있어요."
            if evidence.strip() else "인용문이 제공되지 않았어요."
        )
        c1_items.append(
            substitute_values(
                c1_template,
                {
                    "signal_title": signal_title,
                    "observed": observed,
                    "reason": reason,
                },
            )
        )

    unverified_lines = [
        line for line in (_message_unverified_line(message), _env_unverified_line(env))
        if line
    ]
    official = (analysis_result.get("url") or {}).get("official")
    if official not in ("official", "brand_mismatch"):
        unverified_lines.insert(0, "공식 주소 여부는 확인하지 못했어요.")
    unverified_text = (
        "\n".join(unverified_lines) if unverified_lines
        else "공식 주소 대조와 문자·페이지 분석을 완료했어요."
    )

    c2_item = substitute_values(c2_template, {"unverified": unverified_text})

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": template["template"]["outputs"][0]["carousel"]["type"],
                        "items": [*c1_items, *r9_items, c2_item, c3_template, *guidance_items],
                    }
                }
            ]
        },
    }
