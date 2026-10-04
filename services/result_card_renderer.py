"""분석 결과(`AnalysisResponse` dict) → 완성된 Kakao 카드 JSON.

be_teammate_card_integration_tasks.md 4~6절 기준.
`select_result_card()`로 카드를 고르고, `render_card()`로 값을 채운 뒤,
"자세히 보기" 버튼에 job_id를 꽂는다. 신호 상세는 `render_detail_carousel()`이
별도로 만든다 (카카오 블록 흐름상 "자세히 보기"를 누르면 다른 스킬/블록이
캐러셀을 보여주는 구조라, 결과 카드와 캐러셀은 서로 다른 응답이다).
"""

from typing import Any

from services.chatbot_copy_data import get_signal_copy, get_unverified_copy
from services.kakao_card_renderer import render_card, substitute_values
from services.result_card_selector import select_result_card

DETAIL_BUTTON_LABEL = "자세히 보기"
MAX_CAROUSEL_SIGNAL_ITEMS = 8  # + C2 + C3 = 10장 (카카오 캐러셀 상한)
_EVIDENCE_MAX_LENGTH = 60


def _truncate_evidence(evidence: str) -> str:
    if len(evidence) <= _EVIDENCE_MAX_LENGTH:
        return evidence
    return evidence[: _EVIDENCE_MAX_LENGTH - 1] + "…"


def _inject_job_id(card: dict, job_id: str | None) -> dict:
    """"자세히 보기" 버튼에 `extra.job_id`를 넣는다.

    다른 BE 담당자가 이 job_id로 Job을 찾아서 캐러셀에 넘길 분석 결과를
    조회한다 (Job 조회 로직 자체는 이 함수의 책임이 아니다).
    """

    if job_id is None:
        return card

    for output in card.get("template", {}).get("outputs", []):
        for key in ("textCard", "basicCard"):
            node = output.get(key)
            if not node:
                continue
            for button in node.get("buttons", []) or []:
                if button.get("label") == DETAIL_BUTTON_LABEL:
                    button["extra"] = {"job_id": job_id}

    return card


def _result_card_values(analysis_result: dict, card_name: str) -> dict[str, str]:
    """카드별로 필요한 `{{ placeholder }}` 값을 조립한다.

    org_name은 AI가 식별한 `message.brand`를 그대로 쓴다 — `Brand` enum의
    값 자체가 한글 표준명(예: "CJ대한통운")이라 별도 표시명 매핑이
    필요 없다 (`ai/src/ai/types.py`).
    """

    url = analysis_result.get("url") or {}
    message = analysis_result.get("message") or {}
    brand = message.get("brand")

    values: dict[str, str] = {}

    if brand:
        values["org_name"] = brand

    if card_name == "r2-official":
        domain = url.get("domain")
        if domain:
            values["official_url"] = f"https://{domain}"

    return values


def render_result_card(
    analysis_result: dict,
    job_id: str | None = None,
) -> dict:
    """분석 결과에 맞는 결과 카드를 고르고 완성된 Kakao 응답을 돌려준다."""

    card_name = select_result_card(analysis_result)
    values = _result_card_values(analysis_result, card_name)

    card = render_card(card_name, values)
    return _inject_job_id(card, job_id)


def merge_kakao_responses(responses: list[dict]) -> dict:
    """링크가 여러 개여서 카드가 여러 장 나왔을 때 하나의 응답으로 합친다.

    지금 범위는 "링크 1개"를 전제하므로(`r7-multiple-urls` 라우팅은 이번
    담당 범위 밖이다) 평소에는 입력이 1개뿐이다. 그래도 run_analysis()의
    for 루프가 여러 장을 만들 가능성을 남겨두므로, 응답 형식이 깨지지
    않도록 outputs를 이어 붙인다.
    """

    if not responses:
        return render_card("r4-unavailable")

    if len(responses) == 1:
        return responses[0]

    merged_outputs: list[Any] = []
    for response in responses:
        merged_outputs.extend(
            response.get("template", {}).get("outputs", [])
        )

    return {
        "version": "2.0",
        "template": {"outputs": merged_outputs},
    }


def render_detail_carousel(analysis_result: dict) -> dict:
    """"자세히 보기"에서 쓸 의심 근거 캐러셀을 만든다.

    c-detail-carousel.json의 items[0]을 "의심 근거(C1)" 템플릿으로 보고
    message.details.signals 개수만큼 복제한다. items[1]은 "확인하지 못한
    부분(C2)", items[2]는 "행동 안내(C3)"로 고정 1장씩이다.

    페이지(`env.details.signals`) 신호는 아직 고정 문구가 없어
    (chatbot-copy.md 4절) 이번에는 문자 신호만 카드로 만든다.
    """

    template = render_card("c-detail-carousel")
    items_template = template["template"]["outputs"][0]["carousel"]["items"]
    c1_template, c2_template, c3_template = items_template

    message = analysis_result.get("message") or {}
    signals = (message.get("details") or {}).get("signals") or []

    env = analysis_result.get("env") or {}

    c1_items = []
    for signal in signals[:MAX_CAROUSEL_SIGNAL_ITEMS]:
        code = signal.get("code", "")
        evidence = signal.get("evidence", "")
        signal_title, reason = get_signal_copy(code)

        c1_items.append(
            substitute_values(
                c1_template,
                {
                    "signal_title": signal_title,
                    "observed": f"문자에 '{_truncate_evidence(evidence)}'라고 적혀 있어요.",
                    "reason": reason,
                },
            )
        )

    c2_item = substitute_values(
        c2_template, {"unverified": get_unverified_copy(env.get("answer"))}
    )

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": "textCard",
                        "items": [*c1_items, c2_item, c3_template],
                    }
                }
            ]
        },
    }
