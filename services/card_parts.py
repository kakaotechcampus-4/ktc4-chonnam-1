"""카드 렌더러들이 같이 쓰는 조각: 문구 표, 줄이기, 버튼에 job_id 꽂기, 응답 합치기."""

from typing import Any

from services.kakao_card_renderer import render_card

DETAIL_BUTTON_LABEL = "자세히 보기"
EVIDENCE_MAX_LENGTH = 60
UNKNOWN = "unknown"
NO_RISK = "no_risk_found"
PARTIAL = "partial"
NO_LINES = "- 없음"
TEXT_CARD_MAX_LENGTH = 400


def shorten_evidence(evidence: str, max_length: int = EVIDENCE_MAX_LENGTH) -> str:
    if len(evidence) <= max_length:
        return evidence
    if max_length <= 0:
        return ""
    return evidence[: max_length - 1] + "…"


STOP_ACTION_JOIN = {
    "dont_click_link": "링크를 누르",
    "dont_enter_info": "정보를 입력하",
    "dont_install_app": "앱을 설치하",
    "dont_send_money": "돈을 보내",
    "dont_send_gift_card": "상품권 핀번호를 보내",
    "dont_share_auth_code": "인증번호를 알려 주",
    "dont_call_number_in_message": "상대가 알려 준 번호로 전화하",
}
# response_actions -> 문장 (chatbot-copy.md 3절 "대응" 표. 통화에 나오는 값만 둔다)
RESPONSE_ACTION_LINES = {
    "contact_gift_card_issuer": "상품권 발행사 고객센터에 사용 정지를 요청하세요.",
    "report_112": "112에 신고하세요.",
    "request_payment_stop": "송금한 은행에 지급정지를 요청하세요.",
    "contact_card_issuer": "카드사에 분실·정지 신고를 하세요.",
    "change_password": "입력한 비밀번호를 바꾸세요.",
    "register_info_exposure": "금융감독원 개인정보 노출자 사고예방시스템(pd.fss.or.kr)에 노출 사실을 등록하세요.",
    "isolate_phone": "휴대폰을 끄거나 비행기 모드로 바꾸세요.",
    "use_other_device": "지급정지 신청과 개인정보 노출 등록은\n다른 휴대폰이나 PC로 하세요.",
    "remove_app_and_check_device": "앱을 지우거나 휴대폰을 초기화하세요.",
    "delete_downloaded_file": "받은 파일을 열지 말고 지우세요.",
}
# 돈을 보내지 않았는데 지급정지가 붙는 경우(앱 설치 — 원격 조종 앱이 돈을 빼 갈 수 있다).
# "송금한 은행"은 송금을 전제해서 답과 맞지 않으니 내 계좌를 막으라는 문장으로 바꾼다.
PAYMENT_STOP_WITHOUT_SENDING_LINE = "거래 은행에 계좌 지급정지를 요청하세요."


def response_action_lines(response_actions: list[str], answers: list[str]) -> list[str]:
    """K6 대응 줄. 번호는 실제로 쓴 줄 기준으로 매긴다."""

    sent_money = "sent_money" in answers
    texts = []
    for action in response_actions:
        if action == "request_payment_stop" and not sent_money:
            texts.append(PAYMENT_STOP_WITHOUT_SENDING_LINE)
        elif action in RESPONSE_ACTION_LINES:
            texts.append(RESPONSE_ACTION_LINES[action])
    return [f"{i}. {text}" for i, text in enumerate(texts, start=1)]


# Q1 option_id -> 리스트 카드 항목 썸네일(문자·녹음 공용). 없는 답(clicked_link)은 이미지를 달지 않는다.
Q1_IMAGE_BASE = (
    "https://raw.githubusercontent.com/kakaotechcampus-4/ktc4-chonnam-1/develop/"
    "frontend/kakao-templates/assets/cards/"
)
Q1_OPTION_IMAGES = {
    "entered_info": Q1_IMAGE_BASE + "q1-list-1-entered-info.png",
    "installed_app": Q1_IMAGE_BASE + "q1-list-2-installed-app.png",
    "sent_money": Q1_IMAGE_BASE + "q1-list-3-sent-money.png",
    "not_yet": Q1_IMAGE_BASE + "q1-list-4-not-yet.png",
}


def with_q1_image(item: dict, option_id: str) -> dict:
    image_url = Q1_OPTION_IMAGES.get(option_id)
    return {**item, "imageUrl": image_url} if image_url else item


# Q1 답변 전용 스킬 블록(이슈 #115, 2026-10-29 관리자센터에서 확정).
# "자세히 보기"가 쓰는 블록과는 다른 블록이다 — 그 블록을 재사용하지 않는다.
ANSWER_BLOCK_ID = "6ae336cd25d4bc48e2bb8f2a"


def shorten(text: str, max_length: int = 30) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def address_lines(official: str | None, brand: str | None) -> tuple[str | None, str | None]:
    """멘토 리뷰(PR #54): 기관을 몰라 애초에 대조를 못 한 경우와, 대조했는데
    주소가 일치하지 않는 경우를 구분한다. `check_official_domain()`은
    brand가 없으면 도메인을 보지도 않고 `not_registered`를 반환한다
    (`services/official_domain_service.py`) — 그 도메인이 naver.com처럼
    실제로는 화이트리스트에 있어도 마찬가지다. 그래서 brand 자체가 없을
    때 "공식 주소 목록에 없는 주소예요"라고 하면 사실과 다를 수 있다."""

    has_brand = bool(brand) and brand != UNKNOWN

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


def env_lines(answer: str | None) -> tuple[str | None, str | None]:
    if answer == NO_RISK:
        return "페이지에서 위험 신호를 찾지 못했어요", None

    if answer == PARTIAL:
        return None, "페이지 내용 (끝까지 확인하지 못했어요)"

    if answer == "failed":
        return None, "페이지 내용 (열지 못했어요)"

    if answer == "not_run":
        return None, "페이지 내용"

    return None, None


def inject_job_id(card: dict, job_id: str | None, *, allow_details: bool = True) -> dict:
    """"자세히 보기" 버튼을 허용할 때만 `extra.job_id`를 넣는다.

    다른 BE 담당자가 이 job_id로 Job을 찾아서 캐러셀에 넘길 분석 결과를
    조회한다 (Job 조회 로직 자체는 이 함수의 책임이 아니다).
    링크별 연결이 불가능하면 해당 버튼만 제거하고 나머지 카드 내용은 유지한다.
    """

    if job_id is None and allow_details:
        return card

    for output in card.get("template", {}).get("outputs", []):
        for key in ("textCard", "basicCard"):
            node = output.get(key)
            if not node:
                continue
            if not allow_details:
                node["buttons"] = [
                    button for button in node.get("buttons", []) or []
                    if button.get("label") != DETAIL_BUTTON_LABEL
                ]
                continue
            for button in node.get("buttons", []) or []:
                if button.get("label") == DETAIL_BUTTON_LABEL:
                    button["extra"] = {"job_id": job_id}

    return card


def merge_kakao_responses(responses: list[dict]) -> dict:
    """링크가 여러 개여서 카드가 여러 장 나왔을 때 하나의 응답으로 합친다.

    `r7-multiple-urls` 전용 카드는 `feature/final-fe`에서 지워졌다(이슈
    #139). 링크가 여럿이면 `render_result_card(..., allow_details=...)`가
    "자세히 보기" 버튼만 제거할 뿐 카드 자체는 링크마다 그대로 나오므로,
    `run_analysis()`의 for 루프가 만드는 여러 장을 여기서 하나의 응답으로
    이어 붙인다(개수 제한은 호출부가 담당).
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


def tidy_description(card: dict) -> dict:
    """빈 빈칸이 남긴 앞쪽 빈 줄과 겹친 빈 줄을 정리한다("남는 빈 줄은 BE가 정리한다")."""

    text_card = card["template"]["outputs"][0]["textCard"]
    description = text_card["description"].lstrip("\n")
    while "\n\n\n" in description:
        description = description.replace("\n\n\n", "\n\n")
    text_card["description"] = description
    return card
