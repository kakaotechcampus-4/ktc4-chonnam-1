"""링크 없는 문자 분석 결과(`MessageCaseResult`) 카드: R6b, Q1·Q2 질문, 답변 뒤 K6·A2, 링크 주의 카드.

문구 기준은 docs/designs/chatbot-copy.md 3절 "R6b 링크 없는 문자 결과"·"Q2"·"K6"·"A2"다.
"""

from ai.case.types import Conclusion
from services.kakao_card_renderer import render_card
from services.result_card_selector import select_result_card
from services.card_parts import (
    ANSWER_BLOCK_ID,
    NO_LINES,
    STOP_ACTION_JOIN,
    TEXT_CARD_MAX_LENGTH,
    address_lines,
    env_lines,
    merge_kakao_responses,
    response_action_lines,
    shorten,
    shorten_evidence,
    tidy_description,
    with_q1_image,
)

# 문자는 통화가 아니라서 번호 줄은 "문자 속 번호" 로 쓴다(멈출 행동 표)
_MESSAGE_STOP_ACTION_JOIN = {
    **STOP_ACTION_JOIN,
    "dont_call_number_in_message": "문자 속 번호로 전화하",
}
# v3 §11 6번 요구 종류 라벨. other 는 라벨 없이 뺀다
_MESSAGE_DEMAND_LABELS = {
    "money_transfer": "송금·입금",
    "cash_withdrawal": "현금 인출·전달",
    "gift_card": "상품권 구매",
    "auth_code": "인증번호 전달",
    "personal_info": "정보 입력",
    "app_install": "앱 설치",
    "remote_control": "원격 제어 앱",
    "call_number": "전화 걸기",
    "move_messenger": "메신저 이동",
    "visit_link": "링크·사이트 접속",
}
_MESSAGE_HEADLINES = {
    "suspected": "⚠️ 사칭 문자로 의심돼요",
    "caution": "🔎 주의가 필요한 문자예요",
    "uncertain": "🤔 판단하기 어려워요",
}
_MESSAGE_CALL_KNOWN_LINE = "원래 알던 번호로 직접 전화해 확인하세요."
_MESSAGE_ACCOUNT_HOLDER_LINE = "보내기 전에 받는 계좌가 본인 명의인지 한 번 더 확인하세요."
_MESSAGE_ORG_LINE = "보낸 기관의 공식 앱·홈페이지나 대표번호로 직접 확인하세요."
_MESSAGE_BASIS_NOTE = "답해 주신 내용 기준으로 안내했어요."
_MESSAGE_USER_ANSWER_LINES = {
    "not_person": "- 원래 번호로 통화한 결과 (본인이 아니라고 답해 주셨어요)",
    "is_person": "- 원래 번호로 통화한 결과 (본인이 맞다고 답해 주셨어요)",
}
_MESSAGE_SIMILAR_REASONS = {
    "failed": "사례를 찾다가 문제가 생겼어요",
    "timeout": "시간 안에 끝내지 못했어요",
    "not_run": "이번에는 확인하지 않았어요",
}
# Q1 문자 결과 라벨(3절 Q1 표)과 Q2 라벨(3절 Q2 표)
_MESSAGE_Q1_LABELS = {
    "clicked_link": "링크만 눌렀어요",
    "entered_info": "정보를 입력했어요",
    "installed_app": "앱을 설치했어요",
    "sent_money": "돈이나 상품권을 보냈어요",
    "not_yet": "아무것도 안 했어요",
}
_MESSAGE_Q2_LABELS = {
    "not_person": "본인이 아니래요",
    "is_person": "본인이 맞대요",
    "no_answer": "전화를 안 받아요",
    "not_yet": "아직 안 해 봤어요",
    "unknown": "잘 모르겠어요",
}
_MESSAGE_QUESTION_CARDS = {
    "already_acted": ("q1-already-acted", _MESSAGE_Q1_LABELS),
    "called_known_number": ("q2-called-known-number", _MESSAGE_Q2_LABELS),
}
# K6 문자 결과 버튼(D17): 112 는 돈을 보냈을 때만, 그 밖에는 KISA 상담 하나
_MESSAGE_K6_PHONE_BUTTONS = {
    "report_112": {"label": "112 신고", "action": "phone", "phoneNumber": "112"},
}
_KISA_BUTTON = {
    "label": "KISA 상담하기",
    "action": "webLink",
    "webLinkUrl": "https://www.kisa.or.kr/118",
}
# 멈출 행동이 하나일 때 쓰는 문장(멈출 행동 표 "한 문장으로 쓸 때")
_MESSAGE_STOP_SINGLE = {
    "dont_click_link": "문자 속 링크를 누르지 마세요.",
    "dont_enter_info": "정보를 입력하지 마세요.",
    "dont_install_app": "앱을 설치하지 마세요.",
    "dont_send_money": "돈을 보내지 마세요.",
    "dont_send_gift_card": "상품권을 사거나 핀번호를 보내지 마세요.",
    "dont_share_auth_code": "인증번호를 알려 주지 마세요.",
    "dont_call_number_in_message": "문자 속 번호로 전화하지 마세요.",
}
# A2 의 stop_line 이 빌 때(요구를 읽지 못한 결과에 온 답) 쓰는 문장
_MESSAGE_A2_DEFAULT_STOP = "문자가 요구하는 대로 하지 마세요."


def _message_stop_line(stop_actions: list[str]) -> str:
    """멈출 행동 줄. 받은 순서대로 최대 3개를 "~거나 ~지 마세요." 로 잇는다."""

    actions = [
        a
        for a in [a for a in stop_actions if a not in ("paste_text", "hang_up")][:3]
        if a in _MESSAGE_STOP_ACTION_JOIN
    ]
    if not actions:
        return ""
    if len(actions) == 1 and actions[0] in _MESSAGE_STOP_SINGLE:
        return _MESSAGE_STOP_SINGLE[actions[0]]
    return "거나 ".join(_MESSAGE_STOP_ACTION_JOIN[a] for a in actions) + "지 마세요."


def _message_identity_kinds(result: dict) -> set[str]:
    return {i.get("kind") for i in (result.get("claims") or {}).get("identity") or []}


def _message_verify_line(result: dict, has_stop_line: bool) -> str:
    """R6b 확인 안내 줄(3절 R6b `verify_line`). 가족·지인이면 verify_actions, 그 밖에는 기관 문장이다.

    문자가 내세운 기관 이름과 문자 속 번호는 쓰지 않는다(R9 org_label 과 같음). 멈출 행동이 없고
    주의 갈래가 아니면 이 줄이 첫 줄이 되므로 "문자 속 번호 대신 " 을 앞에 붙인다.
    """

    kinds = _message_identity_kinds(result)
    if "acquaintance" in kinds:
        if "check_account_holder" in (result.get("verify_actions") or []):
            # 본인이라고 확인한 뒤의 안내라 "문자 속 번호 대신" 과 잇지 않는다
            return _MESSAGE_ACCOUNT_HOLDER_LINE
        line = _MESSAGE_CALL_KNOWN_LINE
    else:
        line = _MESSAGE_ORG_LINE
    if not has_stop_line and not result.get("caution"):
        line = "문자 속 번호 대신 " + line
    return line


def _message_claims_block(result: dict, alert: bool) -> str:
    """주장 요약 칸(R6b `message_claims`). 칸 제목까지 포함하고, 줄이 없으면 빈 문자열."""

    claims = result.get("claims") or {}
    identity = claims.get("identity") or []
    demands = claims.get("demands") or []
    pretexts = claims.get("pretexts") or []
    blockers = claims.get("blockers") or []

    lines = []
    relation = [i["quote"] for i in identity if i.get("kind") == "acquaintance" and i.get("quote")]
    institution = [
        i["quote"] for i in identity if i.get("kind") != "acquaintance" and i.get("quote")
    ]
    if relation:
        lines.append(f"- 내세운 관계: '{shorten(relation[0])}'")
    if institution:
        lines.append(f"- 내세운 기관: '{shorten(institution[0])}'")
    # LLM 이 한 문장을 기관과 명분 양쪽으로 읽으면 같은 인용이 두 줄 나온다. 앞 줄과 같은 인용은 뺀다
    reasons = [r for r in [*blockers, *pretexts] if r not in relation[:1] + institution[:1]]
    if reasons:
        title = "내세운 사정" if relation else "내세운 명분"
        lines.append(f"- {title}: '{shorten(reasons[0])}'")
    labels = list(dict.fromkeys(
        _MESSAGE_DEMAND_LABELS[d["kind"]]
        for d in demands
        if d.get("kind") in _MESSAGE_DEMAND_LABELS
    ))[:3]
    if labels:
        lines.append(f"- 요구한 것: {', '.join(labels)}")

    if not lines:
        return ""
    title = "⚠️ 주의할 점" if alert else "📩 문자에 적힌 내용"
    return f"{title}\n" + "\n".join(lines) + "\n\n"


def _message_checked_unchecked(result: dict) -> tuple[str, str]:
    acquaintance = "acquaintance" in _message_identity_kinds(result)
    answer = (result.get("answers") or {}).get("called_known_number")

    checked_lines = []
    for item in result.get("checked") or []:
        if item == "message_text":
            checked_lines.append("- 문자 내용")
        elif item == "similar_cases":
            checked_lines.append("- 비슷한 사례")
        elif item == "user_answer" and answer in _MESSAGE_USER_ANSWER_LINES:
            checked_lines.append(_MESSAGE_USER_ANSWER_LINES[answer])

    unchecked_lines = []
    for entry in result.get("unchecked") or []:
        item, reason = entry.get("item"), entry.get("reason")
        if item == "actual_sender":
            unchecked_lines.append("- 실제로 보낸 사람" if acquaintance else "- 실제로 보낸 곳")
        elif item == "similar_cases":
            reason_label = _MESSAGE_SIMILAR_REASONS.get(reason, "비교할 사례를 찾지 못했어요")
            unchecked_lines.append(f"- 비슷한 사례 ({reason_label})")

    return (
        "\n".join(checked_lines) if checked_lines else "- 없음",
        "\n".join(unchecked_lines) if unchecked_lines else "- 없음",
    )


def render_message_question_card(job_id: str | None, question: dict) -> dict | None:
    """Q1·Q2 리스트 카드. 항목 extra 에 job_id 를 case_id 로 넣는다.

    job_id 가 없으면(동기 개발 경로) 답을 쌓아 둘 곳이 없어 붙이지 않는다.
    """

    if not job_id:
        return None
    question_id = question.get("question_id")
    card_name, labels = _MESSAGE_QUESTION_CARDS.get(question_id, (None, None))
    if card_name is None:
        return None

    items = []
    for option_id in question.get("option_ids") or []:
        if option_id not in labels:
            continue
        item = {
            "title": labels[option_id],
            "action": "block",
            "blockId": ANSWER_BLOCK_ID,
            "extra": {
                "case_id": job_id,
                "question_id": question_id,
                "option_id": option_id,
                # 서버 재시작으로 Job 을 잃어도 문자 결과의 답인지 알 수 있게 한다
                "kind": "message",
            },
        }
        # 썸네일은 Q1 에만 단다. Q2 는 같은 option_id(not_yet)를 써도 뜻이 다르다
        items.append(with_q1_image(item, option_id) if question_id == "already_acted" else item)
    if not items:
        return None
    return render_card(card_name, {"items": items})


def render_message_case_card(result: dict, job_id: str | None = None) -> dict:
    """링크 없는 문자 분석 결과(`MessageCaseResult.model_dump()`)를 카드로 바꾼다.

    - `not_analyzable` -> `r4-unavailable`
    - `input_required`(사칭·요구를 못 찾음) -> `r6-input-required`
    - `suspected` -> `r6b-warning`, `inconclusive` -> `r6b-uncertain` (제목은 caution 으로 고른다)

    붙일 질문(Q1 → Q2 순서, AI 가 정한 순서)이 있으면 결과 카드 아래에 리스트 카드로 붙인다.
    결과 카드 + Q1 + Q2 로 카카오 outputs 상한(3개) 안이다.
    """

    conclusion = result.get("conclusion")

    if conclusion == Conclusion.NOT_ANALYZABLE.value:
        return render_card("r4-unavailable")
    if conclusion == Conclusion.INPUT_REQUIRED.value:
        return render_card("r6-input-required")

    caution = bool(result.get("caution"))
    if conclusion == Conclusion.SUSPECTED.value:
        card_name, headline = "r6b-warning", _MESSAGE_HEADLINES["suspected"]
    else:
        card_name = "r6b-uncertain"
        headline = _MESSAGE_HEADLINES["caution" if caution else "uncertain"]

    alert = conclusion == Conclusion.SUSPECTED.value or caution
    stop_line = _message_stop_line(result.get("stop_actions") or [])
    checked, unchecked = _message_checked_unchecked(result)

    values = {
        "headline": headline,
        "stop_line": stop_line,
        "verify_line": _message_verify_line(result, bool(stop_line)),
        "basis_note": _MESSAGE_BASIS_NOTE if result.get("basis_from_answer") else "",
        "message_claims": _message_claims_block(result, alert),
        "checked": checked,
        "unchecked": unchecked,
    }
    card = tidy_description(render_card(card_name, values))
    if len(card["template"]["outputs"][0]["textCard"]["description"]) > TEXT_CARD_MAX_LENGTH:
        # textCard 설명 상한(400자)을 넘으면 주장 요약을 빼고, 그래도 넘으면 끝을 자른다
        card = tidy_description(render_card(card_name, {**values, "message_claims": ""}))
        text_card = card["template"]["outputs"][0]["textCard"]
        text_card["description"] = shorten_evidence(
            text_card["description"], TEXT_CARD_MAX_LENGTH
        )

    question_cards = [
        qa_card
        for question in result.get("questions") or []
        if (qa_card := render_message_question_card(job_id, question)) is not None
    ]
    if not question_cards:
        return card
    return merge_kakao_responses([card, *question_cards])


def link_caution_applies(analysis_result: dict) -> bool:
    """링크 결과가 판단 보류(R9)이고 주소가 공식이 아닌가 (#89).

    이때만 문자 게이트를 불러 주의 갈래를 고른다. 위험 근거가 있으면 R1, 사칭 패턴이면 R8 로
    이미 정해졌고, 공식 주소면 진짜 택배사 문자에 경고가 나가지 않게 제외한다("공식 주소 일치를 제외한 이유").
    """

    official = (analysis_result.get("url") or {}).get("official")
    return select_result_card(analysis_result) == "r9-uncertain" and official != "official"


def render_link_caution_card(analysis_result: dict, case_result: dict | None) -> dict | None:
    """링크 결과가 판단 보류인데 문자 게이트가 주의라고 하면 R6b 주의 갈래 카드를 만든다 (#89).

    새 카드를 만들지 않고 링크 없는 문자 결과 카드(`r6b-uncertain`)를 그대로 쓴다. 판정은 바꾸지 않고
    판단 보류 안에서 제목과 첫머리만 고른다. 주의가 아니면 None 을 돌려주고 호출부가 R9 를 쓴다.
    링크 결과 아래 Q1 은 미구현(chatbot-copy.md D16)이라 질문 카드는 붙이지 않는다.
    """

    if not case_result or not case_result.get("caution"):
        return None
    if case_result.get("conclusion") != Conclusion.INCONCLUSIVE.value:
        return None

    url = analysis_result.get("url") or {}
    message = analysis_result.get("message") or {}
    env = analysis_result.get("env") or {}
    brand = message.get("brand")

    address_checked, address_unchecked = address_lines(url.get("official"), brand)
    env_checked, env_unchecked = env_lines(env.get("answer"))
    case_checked, case_unchecked = _message_checked_unchecked(case_result)
    checked = [line for line in case_checked.splitlines() if line != NO_LINES]
    checked += [f"- {line}" for line in (address_checked, env_checked) if line]
    unchecked = [line for line in case_unchecked.splitlines() if line != NO_LINES]
    unchecked += [f"- {line}" for line in (address_unchecked, env_unchecked) if line]

    stop_line = _message_stop_line(case_result.get("stop_actions") or [])
    values = {
        "headline": _MESSAGE_HEADLINES["caution"],
        "stop_line": stop_line,
        "verify_line": _message_verify_line(case_result, bool(stop_line)),
        "basis_note": "",
        "message_claims": _message_claims_block(case_result, True),
        "checked": "\n".join(checked) or NO_LINES,
        "unchecked": "\n".join(unchecked) or NO_LINES,
    }
    card = tidy_description(render_card("r6b-uncertain", values))
    if len(card["template"]["outputs"][0]["textCard"]["description"]) > TEXT_CARD_MAX_LENGTH:
        # textCard 설명 상한(400자)을 넘으면 주장 요약을 빼고, 그래도 넘으면 끝을 자른다
        card = tidy_description(render_card("r6b-uncertain", {**values, "message_claims": ""}))
        text_card = card["template"]["outputs"][0]["textCard"]
        text_card["description"] = shorten_evidence(text_card["description"], TEXT_CARD_MAX_LENGTH)
    return card


def _message_k6_card(response_actions: list[str], answers: list[str]) -> dict:
    lines = response_action_lines(response_actions, answers)
    buttons = [
        _MESSAGE_K6_PHONE_BUTTONS[action]
        for action in response_actions
        if action in _MESSAGE_K6_PHONE_BUTTONS
    ][:3]
    acted = {a for a in answers if a not in ("not_yet", "unknown")}
    if not buttons and acted != {"installed_app"}:
        # 버튼 달 대응이 없으면 문자 결과 창구(KISA) 하나. 앱 설치만 고르면 버튼을 달지 않는다(D17)
        buttons = [dict(_KISA_BUTTON)]

    card = render_card("k6-response", {
        "response_lines": "\n".join(lines),
        "contact_line": "",
        "buttons": [dict(b) for b in buttons],
    })
    return tidy_description(card)


def render_message_answer_card(result: dict) -> dict:
    """링크 없는 문자 결과의 Q1 답 뒤 K6(대응)·A2(아직 안 함)를 고른다.

    K6 로 갈지는 방금 누른 답이 아니라 쌓인 답 전부의 `response_actions` 가 비었는지로 정한다(B3).
    """

    response_actions = result.get("response_actions") or []
    answers = (result.get("answers") or {}).get("already_acted") or []

    if response_actions:
        return _message_k6_card(response_actions, answers)

    stop_line = _message_stop_line(result.get("stop_actions") or [])
    return render_card("a2-not-yet", {"stop_line": stop_line or _MESSAGE_A2_DEFAULT_STOP})


def render_message_default_answer_card(response_actions: list[str], option_id: str) -> dict:
    """결과를 찾지 못한 Q1 답. AI 의 default_response_actions 로 K6 를 만든다(B3)."""

    return _message_k6_card(response_actions, [option_id])
