"""분석 결과(`AnalysisResponse` dict) → 완성된 Kakao 카드 JSON.

be_teammate_card_integration_tasks.md 4~6절 기준.
`select_result_card()`로 카드를 고르고, `render_card()`로 값을 채운 뒤,
"자세히 보기" 버튼에 job_id를 꽂는다. 신호 상세는 `render_detail_carousel()`이
별도로 만든다 (카카오 블록 흐름상 "자세히 보기"를 누르면 다른 스킬/블록이
캐러셀을 보여주는 구조라, 결과 카드와 캐러셀은 서로 다른 응답이다).
"""

from typing import Any

from ai.case.types import Conclusion
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


# ============================================================
# 통화 녹음(보이스피싱) 결과 — VoiceCaseResult 기준
# (docs/designs/chatbot-copy.md 3절 "V1 보이스피싱 결과",
#  docs/designs/voice-phishing-flow.md D17)
# ============================================================

_VOICE_STOP_ACTION_JOIN = {
    "dont_click_link": "링크를 누르",
    "dont_enter_info": "정보를 입력하",
    "dont_install_app": "앱을 설치하",
    "dont_send_money": "돈을 보내",
    "dont_send_gift_card": "상품권 핀번호를 보내",
    "dont_share_auth_code": "인증번호를 알려 주",
    "dont_call_number_in_message": "상대가 알려 준 번호로 전화하",
}

_VOICE_DEMAND_LABELS = {
    "money_transfer": "송금·입금",
    "cash_withdrawal": "현금 인출",
    "gift_card": "상품권 구매",
    "auth_code": "인증번호 전달",
    "personal_info": "개인정보 제공",
    "app_install": "앱 설치",
    "remote_control": "원격 제어 앱 연결",
    "call_number": "전화 연결",
    "move_messenger": "메신저 이동",
    "visit_link": "링크 접속",
    "other": "기타 요구",
}

# CheckItem -> "확인한 것" 줄. V1 전용 문구는 표의 V1 칸을 따른다.
_VOICE_CHECKED_LABELS = {
    "transcript": "통화 내용 (받아 적은 글)",
    "phone_number": "전화번호",
    "similar_cases": "비슷한 사례",
    "user_answer": "원래 번호로 통화한 결과",
}

# UncheckedItem -> "확인하지 못한 것" 줄. actual_sender는 V1 전용 문구.
_VOICE_UNCHECKED_LABELS = {
    "actual_sender": "실제로 건 곳",
    "phone_number": "문자 속 전화번호",
    "similar_cases": "비슷한 사례",
    "transcript": "통화 내용 (받아 적은 글)",
    "transcript_accuracy": "받아 적은 글이 정확한지",
    "link_spelling": "주소 철자",
    "link_address": "문자 속 주소",
    "official_domain": "주소가 공식 주소인지",
    "domain_age": "주소가 만들어진 때",
    "page_static": "페이지 내용",
    "page_rendered": "페이지 화면 (직접 열어 본 화면)",
}

_VOICE_UNCHECKED_REASON_LABELS = {
    "skipped_enough_evidence": "근거가 충분해 더 보지 않았어요",
    "not_run": "이번에는 확인하지 않았어요",
    "not_allowed": "페이지를 열지 않기로 하셨어요",
    "no_link": "링크가 없는 문자예요",
    "failed": "열지 못했어요",
    "timeout": "시간 안에 끝내지 못했어요",
    "kb_empty": "비교할 사례를 찾지 못했어요",
    "read_from_image": "사진에서 읽은 글자라 다를 수 있어요",
    "read_from_voice": "말로 들은 주소라 다를 수 있어요",
}

_TRANSCRIPT_ACCURACY_LINE = "- 받아 적은 글이 정확한지 (말을 글로 옮겨 틀릴 수 있어요)"

# response_actions -> 문장 (chatbot-copy.md 3절 "대응" 표. 통화에 나오는 값만 둔다)
_VOICE_RESPONSE_ACTION_LINES = {
    "contact_gift_card_issuer": "상품권 발행사 고객센터에 사용 정지를 요청하세요.",
    "report_112": "112에 신고하세요.",
    "request_payment_stop": "송금한 은행 고객센터에 지급정지를 요청하세요.",
    "contact_card_issuer": "카드사에 분실·정지 신고를 하세요.",
    "change_password": "입력한 비밀번호를 바꾸세요.",
    "register_info_exposure": "금융감독원 개인정보 노출자 사고예방시스템(pd.fss.or.kr)에 노출 사실을 등록하세요.",
    "isolate_phone": "휴대폰 전원을 끄거나 비행기 모드로 바꾸세요.",
    "use_other_device": "지급정지 신청과 개인정보 노출 등록은 다른 휴대폰이나 PC로 하세요.",
    "remove_app_and_check_device": "설치한 앱을 지우거나 휴대폰을 초기화하세요.",
    "delete_downloaded_file": "받은 파일을 열지 말고 지우세요.",
}

# response_actions -> K6 버튼. 표에 없으면 버튼이 없다(report_112만 버튼이 있다).
_VOICE_RESPONSE_ACTION_BUTTON = {
    "report_112": {"label": "112 신고", "action": "phone", "phoneNumber": "112"},
}

# Q1 option_id -> 리스트 카드 항목 라벨(V1 녹음 결과 칸, chatbot-copy.md 3절 Q1)
_VOICE_QA_OPTION_LABELS = {
    "entered_info": "정보를 알려 줬어요",
    "installed_app": "앱을 설치했어요",
    "sent_money": "돈이나 상품권을 보냈어요",
    "not_yet": "아직 안 했어요",
}

# "자세히 보기" 등 기존 카드가 이미 쓰는, /kakao/skill로 돌아오는 블록.
# 이 블록은 clientExtra를 그대로 돌려주기만 하면 돼서, 그 모양(job_id 대
# case_id/question_id/option_id)은 우리 코드가 구분한다 — 새 블록을 꼭
# 새로 만들 필요는 없지만, 실제 배포 전에 관리자센터에서 한 번 확인한다.
_VOICE_CALLBACK_BLOCK_ID = "6aad3abfe745d3358bd90c8d"


def _voice_truncate(text: str, max_length: int = 30) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def _voice_stop_line(stop_actions: list[str]) -> str:
    """멈출 행동 줄. 받은 순서대로 최대 3개, hang_up이 있으면 맨 앞에
    "통화를 끊고, "를 붙인다(녹음이면 늘 hang_up이 들어온다)."""

    ordered = [a for a in stop_actions if a != "paste_text"][:3]
    has_hang_up = "hang_up" in ordered
    joined = [
        _VOICE_STOP_ACTION_JOIN[a] for a in ordered if a in _VOICE_STOP_ACTION_JOIN
    ]

    if not joined:
        return "통화를 끊으세요." if has_hang_up else ""

    line = (joined[0] + "지 마세요.") if len(joined) == 1 else ("거나 ".join(joined) + "지 마세요.")
    return f"통화를 끊고, {line}" if has_hang_up else line


def _voice_verify_line(official_contact: dict | None) -> str:
    if official_contact:
        org = official_contact.get("org", "")
        number = official_contact.get("number", "")
        return f"{org} 대표번호({number})로 직접 전화해 확인하세요."
    return "상대가 말한 기관의 대표번호를 직접 찾아 전화해 확인하세요."


def _voice_call_claims(
    claims: dict,
    caution: bool,
    number_checks: list[dict] | None = None,
    similar_cases: dict | None = None,
) -> str:
    """"주의할 점"/"통화에서 들은 내용" 블록. 비어 있으면 통째로 뺀다.

    상대 번호 공식 대조(`number_checks`)와 비슷한 사례 유무(`similar_cases`)도
    같은 블록 끝에 덧붙인다 — 둘 다 "상대 발화에서 얻은 참고 정보"라는
    점에서 claims와 같은 자리에 둔다.
    """

    identity = claims.get("identity") or []
    pretexts = claims.get("pretexts") or []
    demands = claims.get("demands") or []

    lines = []
    if identity:
        lines.append(f"- 상대가 내세운 기관: '{_voice_truncate(identity[0].get('quote', ''))}'")
    if pretexts:
        lines.append(f"- 상대가 한 말: '{_voice_truncate(pretexts[0])}'")
    if demands:
        demand_labels = [
            _VOICE_DEMAND_LABELS.get(d.get("kind", ""), "기타 요구") for d in demands[:3]
        ]
        lines.append(f"- 요구한 것: {', '.join(demand_labels)}")

    lines.extend(_voice_number_check_lines(number_checks or []))

    similar_line = _voice_similar_cases_line(similar_cases)
    if similar_line:
        lines.append(similar_line)

    if not lines:
        return ""

    title = "주의할 점" if caution else "통화에서 들은 내용"
    return f"{title}\n" + "\n".join(lines) + "\n\n"


_NUMBER_CHECK_RESULT_LABELS = {
    "official": "공식 번호가 맞아요",
    "not_official": "공식 번호가 아니에요",
    # org_not_listed는 "공식이 아니다"가 아니라 "대조할 자료가 없다"는 뜻이다.
    "org_not_listed": "목록에 없는 기관이라 대조하지 못했어요",
}


def _voice_number_check_lines(number_checks: list[dict]) -> list[str]:
    """상대가 말한 번호의 공식 대표번호 대조 줄."""

    lines = []
    for check in number_checks:
        label = _NUMBER_CHECK_RESULT_LABELS.get(check.get("result"))
        if not label:
            continue
        number = check.get("number", "")
        org = check.get("org", "")
        lines.append(f"- 상대가 말한 번호({number})는 {org} {label}")
    return lines


def _voice_similar_cases_line(similar_cases: dict | None) -> str | None:
    """비슷한 사례가 있을 때만 한 줄로 알린다. 유사도 점수는 악성 확률로
    오해될 수 있어 보여 주지 않는다(설계 문서 경고)."""

    if not similar_cases:
        return None
    matches = similar_cases.get("matches") or []
    if not matches:
        return None
    return "- 비슷한 보이스피싱 사례가 있어요"


def _voice_checked_unchecked(result: dict) -> tuple[str, str]:
    checked_lines = [
        f"- {_VOICE_CHECKED_LABELS[item]}"
        for item in result.get("checked") or []
        if item in _VOICE_CHECKED_LABELS
    ]

    unchecked_lines = []
    has_transcript_accuracy = False
    for entry in result.get("unchecked") or []:
        item, reason = entry.get("item"), entry.get("reason")
        if item == "transcript_accuracy":
            has_transcript_accuracy = True
        label = _VOICE_UNCHECKED_LABELS.get(item)
        if not label:
            continue
        if reason == "not_applicable":
            continue
        if reason == "cannot_verify":
            unchecked_lines.append(f"- {label}")
            continue
        reason_label = _VOICE_UNCHECKED_REASON_LABELS.get(reason)
        unchecked_lines.append(f"- {label} ({reason_label})" if reason_label else f"- {label}")

    # 받아 적은 글은 늘 정확성을 장담할 수 없다고 적는다(V1 전용 규칙).
    if not has_transcript_accuracy:
        unchecked_lines.append(_TRANSCRIPT_ACCURACY_LINE)

    return (
        "\n".join(checked_lines) if checked_lines else "- 없음",
        "\n".join(unchecked_lines) if unchecked_lines else "- 없음",
    )


def render_voice_result_card(result: dict, job_id: str | None = None) -> dict:
    """통화 녹음 분석 결과(`VoiceCaseResult.model_dump()`)를 V1 카드로 바꾼다.

    `conclusion`에 따라 카드 자체가 갈린다 — `not_analyzable`은 받아쓰기
    실패 전용 카드(VE3)로 보내고, 그 외에는 V1 한 카드에서 제목만 바뀐다
    (`suspected`/`inconclusive`+`caution`). `questions`가 있으면(Q1 붙는
    조건은 AI 쪽 `decide_call()`이 이미 판단해 둔 값이다) Q1 리스트 카드를
    아래에 붙인다.
    """

    conclusion = result.get("conclusion")
    caution = bool(result.get("caution"))
    checked, unchecked = _voice_checked_unchecked(result)

    if conclusion == Conclusion.NOT_ANALYZABLE.value:
        return render_card("ve3-voice-not-analyzable", {
            "checked": checked,
            "unchecked": unchecked,
        })

    if conclusion == Conclusion.SUSPECTED.value:
        headline = "⚠️ 보이스피싱이 의심돼요"
    elif caution:
        headline = "🔎 주의가 필요한 통화예요"
    else:
        headline = "🤔 판단하기 어려워요"

    card = render_card("v1-voice-result", {
        "headline": headline,
        "stop_line": _voice_stop_line(result.get("stop_actions") or []),
        "verify_line": _voice_verify_line(result.get("official_contact")),
        "call_claims": _voice_call_claims(
            result.get("claims") or {}, caution,
            result.get("number_checks"), result.get("similar_cases"),
        ),
        "checked": checked,
        "unchecked": unchecked,
    })

    questions = result.get("questions") or []
    if not questions:
        return card

    qa_card = render_voice_qa_prompt_card(job_id, questions[0])
    return merge_kakao_responses([card, qa_card]) if qa_card else card


def render_voice_qa_prompt_card(job_id: str | None, question: dict) -> dict | None:
    """Q1 "이미 하신 게 있나요?" 리스트 카드. `case_id`로 job_id를 쓴다.

    job_id가 없으면(동기 개발 경로처럼 Job이 없는 경우) 답을 쌓아 둘 곳이
    없어 카드를 붙이지 않는다.
    """

    if not job_id:
        return None

    option_ids = question.get("option_ids") or []
    items = [
        {
            "title": _VOICE_QA_OPTION_LABELS.get(option_id, option_id),
            "action": "block",
            "blockId": _VOICE_CALLBACK_BLOCK_ID,
            "extra": {
                "case_id": job_id,
                "question_id": "already_acted",
                "option_id": option_id,
            },
        }
        for option_id in option_ids
    ]

    if not items:
        return None

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "listCard": {
                        "header": {"title": "이미 하신 게 있나요?"},
                        "items": items,
                    }
                }
            ]
        },
    }


def render_voice_answer_card(result: dict, answers: list[str]) -> dict:
    """Q1 답변 뒤 K6(대응)·A2(아직 안 함) 카드를 고른다.

    대응 버튼 규칙(D17): `report_112`만 버튼이 있다. 버튼 달 대응이 없으면
    `1394 상담`을 붙이되, 지금까지 고른 답이 `앱을 설치했어요`뿐이면 버튼을
    달지 않는다(그 휴대폰으로 전화를 걸게 되는 걸 막는다).
    """

    response_actions = result.get("response_actions") or []

    if not response_actions:
        return render_card("a2-voice")

    lines = [
        f"{i}. {_VOICE_RESPONSE_ACTION_LINES[action]}"
        for i, action in enumerate(response_actions, start=1)
        if action in _VOICE_RESPONSE_ACTION_LINES
    ]
    card = render_card("k6-voice-response", {"response_lines": "\n".join(lines)})

    buttons = [
        _VOICE_RESPONSE_ACTION_BUTTON[action]
        for action in response_actions
        if action in _VOICE_RESPONSE_ACTION_BUTTON
    ][:3]

    if not buttons and set(answers) != {"installed_app"}:
        buttons = [{"label": "1394 상담", "action": "phone", "phoneNumber": "1394"}]

    card["template"]["outputs"][0]["textCard"]["buttons"] = buttons
    return card


def render_voice_detail_carousel(result: dict) -> dict:
    """통화 녹음 결과의 "자세히 보기". `VoiceCaseResult`는 문자·URL
    결과와 모양이 완전히 달라 `render_detail_carousel()`을 재사용하지
    않는다. 아직 FE 캐러셀 설계가 초안 단계(voice-phishing-flow.md
    "카드 JSON 만들기 전")라, 이미 V1에 쓴 구조화된 값(의심 근거·확인
    범위)만으로 2장짜리 캐러셀을 만든다 — 설계가 확정되면 이 함수만
    바꾸면 된다.
    """

    caution = bool(result.get("caution"))
    claims_block = _voice_call_claims(
        result.get("claims") or {}, caution,
        result.get("number_checks"), result.get("similar_cases"),
    ).strip()
    checked, unchecked = _voice_checked_unchecked(result)

    evidence_body = claims_block if claims_block else "확인된 의심 근거가 없어요."

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "carousel": {
                        "type": "basicCard",
                        "items": [
                            {
                                "basicCard": {
                                    "title": "의심 근거",
                                    "description": evidence_body,
                                }
                            },
                            {
                                "basicCard": {
                                    "title": "이번 분석으로 확인한 것·확인하지 못한 것",
                                    "description": (
                                        f"확인한 것\n{checked}\n\n확인하지 못한 것\n{unchecked}"
                                    ),
                                }
                            },
                        ],
                    }
                }
            ]
        },
    }


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
    return None, None


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

    return None, None


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
    address_checked, address_unchecked = _r9_address_lines(url.get("official"), brand)
    message_checked, message_unchecked = _r9_message_lines(
        message.get("answer"),
        ((message.get("details") or {}).get("reason") or {}).get("failures") or [],
    )
    env_checked, env_unchecked = _r9_env_lines(
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
    unchecked = [
        line for line in (
            sender_unchecked, address_unchecked, message_unchecked, env_unchecked,
        )
        if line
    ]

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
    if answer in _COMPLETE_ANSWERS or answer is None:
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


def render_detail_carousel(analysis_result: dict) -> dict:
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

    signal_budget = max(
        0,
        min(
            MAX_CAROUSEL_SIGNAL_ITEMS,
            MAX_CAROUSEL_TOTAL_ITEMS - _FIXED_CAROUSEL_ITEMS - len(guidance_items),
        ),
    )

    c1_items = []
    for signal in signals[:signal_budget]:
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

    unverified_lines = [
        line for line in (_message_unverified_line(message), _env_unverified_line(env))
        if line
    ]
    unverified_text = "\n".join(unverified_lines) if unverified_lines else "모두 확인했어요."

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
