"""통화 녹음 분석 결과(`VoiceCaseResult`) 카드: V1, Q1 질문, 답변 뒤 K6·A2, 상세 캐러셀.

문구 기준은 docs/designs/chatbot-copy.md 3절 "V1 보이스피싱 결과"다(이슈 #115).
"""

from ai.case.types import Conclusion
from services.chatbot_copy_data import (
    VOICE_SIMILAR_TYPES,
)
from services.kakao_card_renderer import render_card
from services.card_parts import (
    ANSWER_BLOCK_ID,
    STOP_ACTION_JOIN,
    inject_job_id,
    merge_kakao_responses,
    response_action_lines,
    shorten,
    tidy_description,
    with_q1_image,
)

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
# CheckItem -> V1 확인 항목. similar_cases는 실제 검색 결과로 따로 조합한다.
_VOICE_CHECKED_LABELS = {
    "transcript": "통화 내용 (받아 적은 글)",
    "phone_number": "상대가 말한 전화번호",
    "user_answer": "원래 번호로 통화한 결과",
}
# UncheckedItem -> "확인하지 못한 것" 줄. actual_sender는 V1 전용 문구.
# transcript_accuracy는 reason과 무관하게 항상 같은 고정 문장이라 별도 처리한다.
_VOICE_UNCHECKED_LABELS = {
    "actual_sender": "실제로 건 곳",
    "phone_number": "상대가 말한 전화번호",
    "transcript": "통화 내용 (받아 적은 글)",
    "link_spelling": "주소 철자",
    "link_address": "문자 속 주소",
    "official_domain": "주소가 공식 주소인지",
    "domain_age": "주소가 만들어진 때",
    "page_static": "페이지 내용",
    "page_rendered": "페이지 화면 (직접 열어 본 화면)",
}
_TRANSCRIPT_ACCURACY_LINE = "- 받아 적은 글이 정확한지 (틀릴 수 있어요)"
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
# number_checks 결과 -> checked/unchecked 줄(이슈 #115). 있으면 phone_number
# 줄 대신 이걸 쓴다. official·not_official은 확인한 것, org_not_listed는
# 확인하지 못한 것으로 간다 — 쓰는 목록 자체가 다르다.
_NUMBER_CHECK_CHECKED_LINES = {
    "official": "상대가 말한 번호({number}): {org} 대표번호와 같아요 (번호가 같아도 본인이라는 뜻은 아니에요)",
    "not_official": "상대가 말한 번호({number}): {org} 대표번호가 아니에요",
}
_NUMBER_CHECK_UNCHECKED_LINE = "상대가 말한 번호가 {org} 번호인지 (대표번호 자료에 없는 기관이에요)"
# K6: chatbot-copy.md D17. 누적 답에서 송금 > 정보 제공 > 앱 설치 순서.
_VOICE_CONSULT_BUTTON = [{"label": "1394 상담", "action": "phone", "phoneNumber": "1394"}]
# Q1 option_id -> 리스트 카드 항목 라벨(V1 녹음 결과 칸, chatbot-copy.md 3절 Q1)
_VOICE_QA_OPTION_LABELS = {
    "entered_info": "정보를 알려 줬어요",
    "installed_app": "앱을 설치했어요",
    "sent_money": "돈이나 상품권을 보냈어요",
    "not_yet": "아무것도 안 했어요",
}


def _voice_join_stop_actions(action_codes: list[str]) -> str:
    """멈출 행동 코드를 "~거나 ~지 마세요." 한 문장으로 잇는다(최대 3개)."""

    joined = [
        STOP_ACTION_JOIN[a] for a in action_codes[:3] if a in STOP_ACTION_JOIN
    ]
    if not joined:
        return ""
    if len(joined) == 1:
        return joined[0] + "지 마세요."
    return "거나 ".join(joined) + "지 마세요."


def _voice_stop_line(stop_actions: list[str]) -> str:
    """V1 멈출 행동 줄. 받은 순서대로 최대 3개, hang_up이 있으면 맨 앞에
    "통화를 끊고, "를 붙인다(녹음이면 늘 hang_up이 들어온다)."""

    ordered = [a for a in stop_actions if a != "paste_text"][:3]
    has_hang_up = "hang_up" in ordered
    line = _voice_join_stop_actions([a for a in ordered if a != "hang_up"])

    if not line:
        return "통화를 끊으세요." if has_hang_up else ""
    return f"통화를 끊고, {line}" if has_hang_up else line


def _voice_stop_line_excluding_hang_up(stop_actions: list[str]) -> str:
    """A2(아직 안 함) 전용 — 이미 끊은 통화라 hang_up은 아예 뺀다. 없으면
    빈 문자열(이슈 #115)."""

    ordered = [a for a in stop_actions if a not in ("paste_text", "hang_up")][:3]
    return _voice_join_stop_actions(ordered)


def _voice_verify_line(official_contact: dict | None) -> str:
    if official_contact:
        org = official_contact.get("org", "")
        number = official_contact.get("number", "")
        # 실기기 폭에 맞춰 기관 이름·번호 뒤에서 줄을 바꾼다(2026-11-09)
        return f"{org} 대표번호({number})로\n직접 전화해 확인하세요."
    return "통화에 나온 기관의 대표번호를\n직접 찾아 전화해 확인하세요."


def _voice_call_claims(claims: dict, caution: bool) -> str:
    """"주의할 점"/"통화에서 들은 내용" 블록(이슈 #115 V1 call_claims 매핑).

    기관 = identity[].quote, 통화에 나온 말 = 첫 demands[].quote, 통화에
    나온 사정 = blockers(있으면) 또는 pretexts, 통화에 나온 요구 =
    demands[].kind 라벨(최대 3개). 전부 비어 있으면 통째로 빈 문자열
    ("칸 제목 포함, 값 없으면 빈 문자열").

    "내세운 사정 = blockers → pretexts"는 이슈 원문 그대로 "blockers가
    있으면 그걸, 없으면 pretexts"로 읽어 구현했다 — 다르게 뜻했다면
    확인 필요(보고 사항).

    말머리는 이슈 #139에 따라 "통화에 나온 ~"로 기본값을 바꿨다(ADR
    0008로 화자 분리를 아예 없앴으니 "상대가"라고 단정할 근거가 없다).
    예전에는 `speaker_unknown`일 때만 이 말머리로 바꾸는 치환 루프가
    있었다 — ADR 0008이 채택되고(2026-11-07) #116이 "화자 분리 코드
    자체가 없어져 speaker/speaker_unknown을 결과에 남길 필요가 없다"며
    닫혀(2026-11-10), 그 분기가 영영 쓰일 수 없다는 게 확정돼 지웠다
    (이슈 #139). 같은 이유로 `_voice_verify_line()`·`claim_note`의
    `speaker_unknown` 분기와 `_voice_speaker_unknown()` 자체도 PR #167
    리뷰에 따라 함께 지웠다.
    """

    identity = claims.get("identity") or []
    demands = claims.get("demands") or []
    pretexts = claims.get("pretexts") or []
    blockers = claims.get("blockers") or []

    lines = []
    if identity:
        quotes = [shorten(i.get("quote", "")) for i in identity if i.get("quote")]
        if quotes:
            quoted = [f"'{q}'" for q in quotes]
            lines.append(f"- 통화에 나온 기관: {', '.join(quoted)}")
    if demands and demands[0].get("quote"):
        lines.append(f"- 통화에 나온 말: '{shorten(demands[0]['quote'])}'")
    pretext_source = blockers or pretexts
    if pretext_source:
        lines.append(f"- 통화에 나온 사정: '{shorten(pretext_source[0])}'")
    if demands:
        demand_labels = [
            _VOICE_DEMAND_LABELS.get(d.get("kind", ""), "기타 요구") for d in demands[:3]
        ]
        lines.append(f"- 통화에 나온 요구: {', '.join(demand_labels)}")

    if not lines:
        return ""

    title = "⚠️ 주의할 점" if caution else "📞 통화에서 들은 내용"
    return f"{title}\n" + "\n".join(lines) + "\n\n"


def _voice_number_check_entries(number_checks: list[dict]) -> tuple[list[str], list[str]]:
    """number_checks를 checked/unchecked 줄로 바꾼다(이슈 #115). 있으면
    일반 phone_number 줄 대신 이 줄을 쓴다."""

    checked_lines, unchecked_lines = [], []
    for check in number_checks[:2]:
        number, org, result = check.get("number", ""), check.get("org", ""), check.get("result")
        template = _NUMBER_CHECK_CHECKED_LINES.get(result)
        if template:
            checked_lines.append(f"- {template.format(number=number, org=org)}")
        elif result == "org_not_listed":
            unchecked_lines.append(f"- {_NUMBER_CHECK_UNCHECKED_LINE.format(org=org)}")
    return checked_lines, unchecked_lines


def _voice_similar_cases_state(result: dict) -> str:
    """검색 실패와 정상 검색의 빈 결과를 구분한다. 일치 유형은 추측하지 않는다."""
    similar = result.get("similar_cases") or {}
    if any(
        entry.get("item") == "similar_cases" and entry.get("reason") in {"failed", "timeout"}
        for entry in result.get("unchecked") or []
    ):
        return "failed"
    if similar.get("status") == "completed" and similar.get("matches"):
        return "found"
    return "empty"


def _voice_checked_unchecked(result: dict) -> tuple[str, str]:
    number_checks = result.get("number_checks") or []
    nc_checked, nc_unchecked = _voice_number_check_entries(number_checks)
    skip_phone_number = bool(number_checks)

    checked_lines = list(nc_checked)
    for item in result.get("checked") or []:
        if item == "similar_cases" or (item == "phone_number" and skip_phone_number):
            continue  # 유사 사례와 번호 확인은 각각 실제 결과로 따로 조합한다.
        label = _VOICE_CHECKED_LABELS.get(item)
        if label:
            checked_lines.append(f"- {label}")

    unchecked_lines = list(nc_unchecked)
    has_transcript_accuracy = False
    for entry in result.get("unchecked") or []:
        item, reason = entry.get("item"), entry.get("reason")
        if item == "similar_cases" or (item == "phone_number" and skip_phone_number):
            continue
        if item == "transcript_accuracy":
            has_transcript_accuracy = True
            unchecked_lines.append(_TRANSCRIPT_ACCURACY_LINE)
            continue
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

    similar_state = _voice_similar_cases_state(result)
    if similar_state == "found":
        checked_lines.append("- 비슷한 사례")
    else:
        reason = (
            "사례를 찾다가 문제가 생겼어요" if similar_state == "failed"
            else "비교할 사례를 찾지 못했어요"
        )
        unchecked_lines.append(f"- 비슷한 사례 ({reason})")

    # 받아 적은 글은 늘 정확성을 장담할 수 없다고 적는다(V1 전용 규칙).
    if not has_transcript_accuracy:
        unchecked_lines.append(_TRANSCRIPT_ACCURACY_LINE)

    return (
        "\n".join(checked_lines) if checked_lines else "- 없음",
        "\n".join(unchecked_lines) if unchecked_lines else "- 없음",
    )


def render_voice_result_card(result: dict, job_id: str | None = None) -> dict:
    """통화 녹음 분석 결과(`VoiceCaseResult.model_dump()`)를 결과 카드로
    바꾼다(이슈 #115). `conclusion`에 따라 카드 자체가 갈린다:

    - `not_analyzable` -> `ve3-unrecognized`(받아쓰기 실패, 고정 문구)
    - `input_required` -> `ve4-no-claim`(사칭·요구를 못 찾음, 고정 문구)
    - 그 외 -> `v1-voice-result` 한 카드에서 제목만 바뀐다
      (`suspected`/`inconclusive`+`caution`)

    `questions`가 있으면(Q1 붙는 조건은 AI 쪽 `decide_call()`이 이미
    판단해 둔 값이다 — `input_required`는 구조적으로 빈 리스트만 온다)
    Q1 리스트 카드를 아래에 붙인다.
    """

    conclusion = result.get("conclusion")

    if conclusion == Conclusion.NOT_ANALYZABLE.value:
        return render_card("ve3-unrecognized")

    if conclusion == Conclusion.INPUT_REQUIRED.value:
        return render_card("ve4-no-claim")

    caution = bool(result.get("caution"))
    checked, unchecked = _voice_checked_unchecked(result)

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
        "call_claims": _voice_call_claims(result.get("claims") or {}, caution),
        "checked": checked,
        "unchecked": unchecked,
    })

    card = inject_job_id(tidy_description(card), job_id)
    questions = result.get("questions") or []
    if not questions:
        return card

    qa_card = render_voice_qa_prompt_card(job_id, questions[0])
    return merge_kakao_responses([card, qa_card]) if qa_card else card


def render_voice_qa_prompt_card(job_id: str | None, question: dict) -> dict | None:
    """Q1 "이미 하신 게 있나요?" 리스트 카드(`q1-already-acted.json`).
    `case_id`로 job_id를 쓴다. `items`는 FE 템플릿에서 문자열 통째
    빈칸이라, 렌더러(`kakao_card_renderer._substitute_string`)가 타입을
    보존해 리스트 그대로 치환한다.

    job_id가 없으면(동기 개발 경로처럼 Job이 없는 경우) 답을 쌓아 둘 곳이
    없어 카드를 붙이지 않는다.
    """

    if not job_id:
        return None

    option_ids = question.get("option_ids") or []
    items = [
        with_q1_image({
            "title": _VOICE_QA_OPTION_LABELS.get(option_id, option_id),
            "action": "block",
            "blockId": ANSWER_BLOCK_ID,
            "extra": {
                "case_id": job_id,
                "question_id": "already_acted",
                "option_id": option_id,
            },
        }, option_id)
        for option_id in option_ids
    ]

    if not items:
        return None

    return render_card("q1-already-acted", {"items": items})


def _voice_d17_buttons(answers: list[str]) -> list[dict]:
    """D17 버튼 우선순위. not_yet은 앞서 선택한 행동을 철회하지 않는다."""

    given = set(answers)
    if "sent_money" in given:
        return [{"label": "112 신고", "action": "phone", "phoneNumber": "112"}]
    if "entered_info" in given:
        return list(_VOICE_CONSULT_BUTTON)
    if "installed_app" in given:
        return []
    return list(_VOICE_CONSULT_BUTTON)


def render_voice_answer_card(result: dict, answers: list[str]) -> dict:
    """Q1 답변 뒤 K6(대응)·A2(아직 안 함) 카드를 고른다(이슈 #115).
    문자 결과(D17, `response_actions` 코드 기준)는 이 함수와 무관하다 —
    음성 전용이다.
    """

    response_actions = result.get("response_actions") or []

    if not response_actions:
        return tidy_description(render_card("a2-not-yet-voice", {
            "stop_line": _voice_stop_line_excluding_hang_up(result.get("stop_actions") or []),
        }))

    lines = response_action_lines(response_actions, answers)
    buttons = _voice_d17_buttons(answers)
    # 1394 버튼이 있을 때만 PC 사용자에게도 상담 번호를 보여 준다.
    contact_line = (
        "상담: 피싱 통합 상담 1394 (24시간)"
        if any(button.get("phoneNumber") == "1394" for button in buttons) else ""
    )

    # contact_line 이 비면 템플릿이 빈 줄을 하나 더 남긴다. 정리한다.
    return tidy_description(render_card("k6-response", {
        "response_lines": "\n".join(lines),
        "contact_line": contact_line,
        "buttons": buttons,
    }))


def render_voice_detail_carousel(result: dict) -> dict:
    """통화 녹음 결과의 "자세히 보기"(`c-detail-carousel-voice.json`,
    이슈 #115). `VoiceCaseResult`는 문자·URL 결과와 모양이 완전히 달라
    `render_detail_carousel()`을 재사용하지 않는다.

    `claim_lines`는 `call_claims`에서 칸 제목(첫 줄)만 뺀 줄들이다.
    """

    caution = bool(result.get("caution"))
    claims_block = _voice_call_claims(result.get("claims") or {}, caution).strip()
    claim_lines = (
        "\n".join(claims_block.splitlines()[1:]) if claims_block else "확인된 의심 근거가 없어요."
    )
    _, unchecked = _voice_checked_unchecked(result)
    verify_line = _voice_verify_line(result.get("official_contact"))

    # #109: 가장 닮은 사례 하나(matches[0])의 type 으로 문구를 고른다. 표에 없는 유형이면
    # 수법을 추측하지 않고 사례를 찾았다는 사실만 알린다.
    top_type = ((result.get("similar_cases") or {}).get("matches") or [{}])[0].get("type") or ""
    similar_type, similar_line = VOICE_SIMILAR_TYPES.get(top_type, ("", ""))

    card = render_card("c-detail-carousel-voice", {
        "claim_lines": claim_lines,
        "claim_note": "상대가 한 말로 보이는 문장만 근거로 썼어요.",
        "similar_type": similar_type,
        "similar_line": similar_line,
        "unchecked": unchecked,
        "verify_line": verify_line,
    })
    items = card["template"]["outputs"][0]["carousel"]["items"]
    if _voice_similar_cases_state(result) != "found":
        items.pop(1)
        return card
    if top_type in VOICE_SIMILAR_TYPES:
        # similar_line 이 빈 유형(other)은 남는 빈 줄을 정리한다("남는 빈 줄은 BE가 정리한다")
        items[1]["description"] = items[1]["description"].replace("\n\n\n", "\n\n")
    else:
        items[1]["description"] = (
            "이번 통화와 비슷한 예시 통화를 찾았어요.\n\n"
            "비슷하다고 해서 사기라는 뜻은 아니에요.\n결과 판단에는 쓰지 않았어요."
        )
    return card
