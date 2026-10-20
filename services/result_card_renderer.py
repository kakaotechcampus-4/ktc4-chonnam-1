"""분석 결과(`AnalysisResponse` dict) → 완성된 Kakao 카드 JSON.

be_teammate_card_integration_tasks.md 4~6절 기준.
`select_result_card()`로 카드를 고르고, `render_card()`로 값을 채운 뒤,
"자세히 보기" 버튼에 job_id를 꽂는다. 신호 상세는 `render_detail_carousel()`이
별도로 만든다 (카카오 블록 흐름상 "자세히 보기"를 누르면 다른 스킬/블록이
캐러셀을 보여주는 구조라, 결과 카드와 캐러셀은 서로 다른 응답이다).
"""

from typing import Any

from services.chatbot_copy_data import (
    get_message_failure_copy,
    get_signal_copy,
    get_unverified_copy,
)
from services.official_domain_service import official_homepage
from services.kakao_card_renderer import render_card, substitute_values
from services.result_card_selector import select_result_card

try:
    # 이슈 #69: ai.kb.guidance는 이 글을 쓰는 시점에 feature/rag-fix-ai에만
    # 있고 develop에는 아직 merge되지 않았다. merge 전까지는 빈 목록으로
    # 동작해 "자세히 보기" 자체는 먼저 쓸 수 있게 하고, AI 쪽이 merge되면
    # BE 코드를 더 바꾸지 않아도 안내 카드가 자동으로 붙는다.
    from ai.kb.guidance import find_guidance
except ImportError:
    def find_guidance(final_payload: Any) -> list:
        return []

DETAIL_BUTTON_LABEL = "자세히 보기"
MAX_CAROUSEL_SIGNAL_ITEMS = 8  # + C2 + C3 = 10장 (카카오 캐러셀 상한)
MAX_CAROUSEL_TOTAL_ITEMS = 10  # 카카오 캐러셀 자체 상한
_FIXED_CAROUSEL_ITEMS = 2  # C2(미확인) + C3(행동 안내)는 항상 1장씩 나간다
_MAX_GUIDANCE_ITEMS = 3  # find_guidance() 계약(최대 3개)과 별개로 BE에서도 한 번 더 막는다
_GUIDANCE_TEXT_MAX_LENGTH = 128  # FE 합의: 카드 1장당 128자
_EVIDENCE_MAX_LENGTH = 60

_UNKNOWN = "unknown"
_MAX_REQUESTED_ACTIONS = 3
_NO_RISK = "no_risk_found"
_PARTIAL = "partial"
_R9_NO_LINES = "- 없음"
_R9_CARD_MAX_LENGTH = 400
_R9_DEFAULT_MESSAGE_REASON = "문자 분석 결과를 전달받지 못했습니다."


def _truncate_evidence(evidence: str, max_length: int = _EVIDENCE_MAX_LENGTH) -> str:
    if len(evidence) <= max_length:
        return evidence
    if max_length <= 0:
        return ""
    return evidence[: max_length - 1] + "…"


def _r9_org_label(brand: str | None) -> str:
    if brand and brand != _UNKNOWN:
        return brand
    return "보낸 기관의"


def _r9_sender_lines(brand: str | None) -> tuple[str | None, str]:
    """보낸 곳은 항상 '확인하지 못한 것'에 들어간다 (PR #39 멘토 리뷰:
    "검찰청이라고 주장한다"와 "검찰청이 보냈다"를 구분하라는 지적)."""
    if brand and brand != _UNKNOWN:
        return f"문자에 적힌 보낸 곳: {brand}", "실제로 보낸 곳"
    return None, "실제로 보낸 곳"


def _r9_requested_actions_line(doubts: list[dict]) -> str | None:
    values = [
        doubt.get("value") for doubt in doubts
        if doubt.get("value") and doubt.get("value") != _UNKNOWN
    ]
    if not values:
        return None
    joined = ", ".join(values[:_MAX_REQUESTED_ACTIONS])
    return f"문자에서 요구한 것: {joined}"


def _r9_address_lines(official: str | None, brand: str | None) -> tuple[str | None, str | None]:
    """멘토 리뷰(PR #54): 기관을 몰라 애초에 대조를 못 한 경우와, 대조했는데
    주소가 일치하지 않는 경우를 구분한다. `check_official_domain()`은
    brand가 없으면 도메인을 보지도 않고 `not_registered`를 반환한다
    (`services/official_domain_service.py`) — 그 도메인이 naver.com처럼
    실제로는 화이트리스트에 있어도 마찬가지다. 그래서 brand 자체가 없을
    때 "공식 주소 목록에 없는 주소예요"라고 하면 사실과 다를 수 있다."""

    has_brand = bool(brand) and brand != _UNKNOWN

    if official == "official":
        if has_brand:
            return f"주소가 {brand} 공식 주소와 같아요", None
        return "주소가 공식 주소 목록에 있어요", None
    if official == "not_registered":
        if not has_brand:
            return None, "주소가 공식 주소인지 (보낸 기관을 몰라 대조하지 못했어요)"
        return None, "주소가 공식 주소인지 (공식 주소 목록에 없는 기관이라 대조하지 못했어요)"
    if official == "brand_mismatch":
        return "공식 주소 목록에 없는 주소예요", None
    if official == "unresolved":
        return None, "주소가 공식 주소인지"
    return None, "주소가 공식 주소인지"


def _r9_message_lines(
    answer: str | None, failures: list[str]
) -> tuple[str | None, str | None]:
    """이슈 #59: `partial`/`failed`는 answer 이름이 아니라 AI가 보존한
    실제 실패 원인(`failures[0]`)으로 설명한다 — "분석을 끝내지
    못했어요" 같은 뭉뚱그린 문구 대신 "시간 초과라 못 봤다"처럼
    구체적으로 말한다. 원인 정보가 없는 기존 응답도 기본 안내로
    떨어지니 깨지지 않는다(`get_message_failure_copy`)."""

    if answer == _NO_RISK:
        return "문자에서 위험 신호를 찾지 못했어요", None

    if answer in (_PARTIAL, "failed"):
        return None, f"문자 내용 ({get_message_failure_copy(failures)})"

    if answer == "not_run":
        return None, "문자 내용"

    if answer == "risk_found":
        return None, None
    return None, "문자 내용"


def _r9_env_lines(
    answer: str | None, failures: list[str]
) -> tuple[str | None, str | None]:
    if answer == _NO_RISK:
        return "페이지에서 위험 신호를 찾지 못했어요", None

    if answer == _PARTIAL:
        return None, "페이지 내용 (끝까지 확인하지 못했어요)"

    if answer == "failed":
        return None, "페이지 내용 (열지 못했어요)"

    if answer == "not_run":
        return None, "페이지 내용"

    if answer == "risk_found":
        return None, None
    return None, "페이지 내용"


def _unverified_lines(analysis_result: dict, *, detail: bool = False) -> list[str]:
    """첫 카드와 C2가 같은 항목을 미확인으로 선택하고 표시 형식만 달리한다."""
    message = analysis_result.get("message") or {}
    env = analysis_result.get("env") or {}
    brand = message.get("brand")
    _, sender = _r9_sender_lines(brand)
    _, address = _r9_address_lines((analysis_result.get("url") or {}).get("official"), brand)
    _, message_line = _r9_message_lines(
        message.get("answer"),
        ((message.get("details") or {}).get("reason") or {}).get("failures") or [],
    )
    _, env_line = _r9_env_lines(
        env.get("answer"),
        ((env.get("details") or {}).get("reason") or {}).get("failures") or [],
    )
    if detail:
        message_line = _message_unverified_line(message) if message_line else None
        env_line = _env_unverified_line(env) if env_line else None
    return [line for line in (sender, address, message_line, env_line) if line]


def _r9_blank_values(analysis_result: dict) -> dict[str, str]:
    """r9-uncertain의 `{{ org_label }}`, `{{ checked }}`, `{{ unchecked }}`를 채운다.

    docs/designs/chatbot-copy.md "R9 판단 보류 → 빈칸 채우는 규칙"의 표를
    그대로 따른다. 행 순서: 보낸 곳 → 요구 행동 → 주소 → 문자 → 페이지.
    """

    url = analysis_result.get("url") or {}
    message = analysis_result.get("message") or {}
    env = analysis_result.get("env") or {}
    brand = message.get("brand")

    sender_checked, _ = _r9_sender_lines(brand)
    requested_checked = _r9_requested_actions_line(
        (message.get("details") or {}).get("doubts") or []
    )
    address_checked, _ = _r9_address_lines(url.get("official"), brand)
    message_checked, _ = _r9_message_lines(
        message.get("answer"),
        ((message.get("details") or {}).get("reason") or {}).get("failures") or [],
    )
    env_checked, _ = _r9_env_lines(
        env.get("answer"),
        ((env.get("details") or {}).get("reason") or {}).get("failures") or [],
    )

    checked = [
        line for line in (
            sender_checked, requested_checked, address_checked,
            message_checked, env_checked,
        )
        if line
    ]
    unchecked = _unverified_lines(analysis_result)

    return {
        "org_label": _r9_org_label(brand),
        "checked": "\n".join(f"- {line}" for line in checked) or _R9_NO_LINES,
        "unchecked": "\n".join(f"- {line}" for line in unchecked) or _R9_NO_LINES,
    }


def _r9_message_reason(message: dict) -> str:
    """이슈 #68: `message.brand`는 택배사만 알아보므로, 청소행정과처럼
    택배사가 아닌 기관이 내세운 명분·요구는 이 칸으로만 전달된다.
    확인한 사실이 아니라 "문자가 주장한 내용"이라 기본값도 '확인'이
    아니라 '전달받지 못했습니다'로 둔다."""

    reason = ((message.get("details") or {}).get("reason") or {}).get("text")
    reason = (reason or "").strip()
    return reason or _R9_DEFAULT_MESSAGE_REASON


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
        available = _R9_CARD_MAX_LENGTH - len(fixed)
        node["description"] = substitute_values(
            node["description"],
            {"message_reason": _truncate_evidence(reason, available)},
        )

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


_COMPLETE_ANSWERS = (_NO_RISK, "risk_found")


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


def _guidance_description(guidance: Any) -> str:
    """이슈 #69: `kind`에 따라 FE가 정한 두 형식 중 하나로 문장을 만든다.

    `procedure`는 "{source}는 ~라고 안내했어요({연도})" 형식이다 — 문자가
    가짜라는 결론이 아니라 공식 기관이 실제로 안내한 절차임을 보여주는
    것이 목적이라, 여기서 "그래서 가짜/진짜"라는 판단을 덧붙이지 않는다.
    `Guidance.text`는 128자를 넘을 수 있어(이슈 댓글: 택배 고객센터
    5곳·카드사 9곳 번호 포함) 잘라낸다 — 여러 장으로 쪼개는 규칙은 FE와
    아직 합의되지 않아, 합의 전까지는 안전하게 자르는 쪽을 택한다.
    """

    kind = getattr(guidance, "kind", None)
    text = getattr(guidance, "text", None) or ""

    if kind == "procedure":
        source = getattr(guidance, "source", None) or "관련 기관"
        published_at = getattr(guidance, "published_at", None) or ""
        year = published_at[:4] if published_at else ""
        year_suffix = f"({year})" if year else ""
        text = f"{source}는 {text}라고 안내했어요{year_suffix}."

    return _truncate_evidence(text, _GUIDANCE_TEXT_MAX_LENGTH)


def _guidance_items(analysis_result: dict) -> list[dict]:
    """최종 분석 결과로 `find_guidance()`를 불러 캐러셀 항목을 만든다.

    이슈 계약상 `find_guidance()`는 예외를 던지지 않지만, 아직 merge되지
    않은 AI 쪽 코드를 믿고 그 계약에만 기대지 않는다 — 안내 카드 하나
    때문에 "자세히 보기" 전체가 깨지면 안 된다(CLAUDE.md 절대 원칙 3과
    같은 이유: 부가 기능 실패가 핵심 응답을 막으면 안 된다).
    """

    try:
        guidance_list = find_guidance(analysis_result) or []
    except Exception as e:
        print(f"[GUIDANCE ERROR] {type(e).__name__}: {e}")
        return []

    if not guidance_list:
        return []

    template = render_card("c4-guidance")
    item_template = template["template"]["outputs"][0]["carousel"]["items"][0]

    return [
        substitute_values(item_template, {"guidance_text": _guidance_description(guidance)})
        for guidance in guidance_list[:_MAX_GUIDANCE_ITEMS]
    ]


def _detail_signals(analysis_result: dict) -> list[tuple[str, dict]]:
    """검증된 signals만 출처별 중복 제거 후 교대로 배치한다.

    한쪽 신호가 많아도 다른 출처가 카드 상한 밖으로 밀리지 않게 한다.
    doubts/reason은 위험 신호로 승격하지 않는다.
    """
    sources = []
    for part_name, label in (("message", "문자"), ("env", "페이지")):
        part = analysis_result.get(part_name) or {}
        unique = []
        seen = set()
        for signal in (part.get("details") or {}).get("signals") or []:
            code, evidence = signal.get("code"), signal.get("evidence")
            if not isinstance(code, str) or not code or not isinstance(evidence, str) or not evidence.strip():
                continue
            key = (code, evidence)
            if key not in seen:
                seen.add(key)
                unique.append((label, signal))
        sources.append(unique)
    ordered = []
    for index in range(max(map(len, sources))):
        for source in sources:
            if index < len(source):
                ordered.append(source[index])
    return ordered


def render_detail_carousel(analysis_result: dict) -> dict:
    """"자세히 보기"에서 쓸 의심 근거 캐러셀을 만든다.

    c-detail-carousel.json의 items[0]을 "의심 근거(C1)" 템플릿으로 보고
    message/env.details.signals의 검증된 근거마다 복제한다. items[1]은 "확인하지 못한
    부분(C2)", items[2]는 "행동 안내(C3)"로 고정 1장씩이다. 이슈 #69로
    추가된 공식 확인 방법 카드(`find_guidance()` 결과)는 맨 뒤에 붙인다.

    문자와 페이지의 출처를 구분해 표시하고, 같은 출처의 중복 신호는 생략한다.

    전체 10장 상한(카카오 캐러셀 자체 제약)은 C2·C3 2장을 먼저 빼고
    남는 자리를 신호 카드와 안내 카드가 나눠 쓴다 — 안내 카드가 있으면
    신호 카드 상한(`MAX_CAROUSEL_SIGNAL_ITEMS`)이 그만큼 줄어든다.
    """

    template = render_card("c-detail-carousel")
    items_template = template["template"]["outputs"][0]["carousel"]["items"]
    c1_template, c2_template, c3_template = items_template

    signals = _detail_signals(analysis_result)

    guidance_items = _guidance_items(analysis_result)

    signal_budget = max(
        0,
        min(
            MAX_CAROUSEL_SIGNAL_ITEMS,
            MAX_CAROUSEL_TOTAL_ITEMS - _FIXED_CAROUSEL_ITEMS - len(guidance_items),
        ),
    )

    c1_items = []
    for source, signal in signals[:signal_budget]:
        code = signal.get("code", "")
        evidence = signal.get("evidence", "")
        signal_title, reason = get_signal_copy(code)
        if source == "페이지":
            signal_title = signal_title.replace("문자에서", "페이지에서", 1)
            observed = f"페이지에서 확인한 내용: {_truncate_evidence(evidence)}"
        else:
            observed = f"문자에 '{_truncate_evidence(evidence)}'라고 적혀 있어요."

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

    unverified_text = "\n".join(_unverified_lines(analysis_result, detail=True))

    c2_item = substitute_values(c2_template, {"unverified": unverified_text})

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": template["template"]["outputs"][0]["carousel"]["type"],
                        "items": [*c1_items, c2_item, c3_template, *guidance_items],
                    }
                }
            ]
        },
    }
