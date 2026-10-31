"""근거 코드별 템플릿 문장 (v3 §7.10). LLM 없이 문장을 만든다.

안심을 약속하는 말은 쓰지 않는다. 금지어는 테스트가 고정한다.
"""

from __future__ import annotations

from ai.case.types import Conclusion, Evidence, EvidenceCode

# 출력 순서도 이 표 순서다
TEMPLATES: dict[EvidenceCode, str] = {
    EvidenceCode.INSTITUTION_CLAIM: "상대가 기관을 내세웠지만, 통화만으로 실제 기관인지는 확인할 수 없어요.",
    EvidenceCode.ACQUAINTANCE_CLAIM: "상대가 아는 사람이라고 했지만, 통화만으로 본인인지는 확인할 수 없어요.",
    EvidenceCode.MONEY_REQUEST: "상대가 돈을 보내라고 요구했어요.",
    EvidenceCode.CASH_DELIVERY_REQUEST: "상대가 현금을 찾아 전달하라고 요구했어요.",
    EvidenceCode.GIFT_CARD_REQUEST: "상대가 상품권을 요구했어요.",
    EvidenceCode.AUTH_CODE_REQUEST: "상대가 인증번호를 알려 달라고 요구했어요.",
    EvidenceCode.INFO_REQUEST: "상대가 개인정보를 알려 달라고 요구했어요.",
    EvidenceCode.APP_INSTALL_REQUEST: "상대가 앱 설치를 요구했어요.",
    EvidenceCode.REMOTE_APP_MENTION: "상대가 원격 조종 앱을 언급했어요.",
    EvidenceCode.CALL_REQUEST: "상대가 특정 번호로 전화하라고 했어요.",
    EvidenceCode.MESSENGER_MOVE: "상대가 메신저로 옮겨 대화하자고 했어요.",
    EvidenceCode.CONTACT_BLOCKER: "상대가 다른 사람과 연락하지 못하게 막으려 했어요.",
    EvidenceCode.UNOFFICIAL_NUMBER: "상대가 말한 번호가 내세운 기관의 공식 대표번호와 달랐어요.",
}

LEADS: dict[Conclusion, str] = {
    Conclusion.SUSPECTED: "보이스피싱이 의심돼요.",
    Conclusion.INCONCLUSIVE: "판단하기 어려워요.",
    Conclusion.INPUT_REQUIRED: "상대가 내세운 기관이나 요구를 찾지 못했어요. 통화 내용을 더 알려 주세요.",
    Conclusion.NOT_ANALYZABLE: "통화 내용을 받아 적지 못해 살펴볼 수 없었어요.",
    Conclusion.OFFICIAL_DOMAIN: "",
}


def explain_call(conclusion: Conclusion, evidence: list[Evidence]) -> str:
    """결론 한 줄과 근거 코드별 문장을 이어 붙인다."""
    codes = {e.code for e in evidence}
    lines = [LEADS[conclusion]]
    lines += [t for c, t in TEMPLATES.items() if c in codes]
    return " ".join(line for line in lines if line)


# 링크 없는 문자. 신분 문장만 "통화" 대신 "문자" 로 바꾸고 답 근거 문장을 더한다
MESSAGE_TEMPLATES: dict[EvidenceCode, str] = {
    **TEMPLATES,
    EvidenceCode.INSTITUTION_CLAIM: "보낸 사람이 기관을 내세웠지만, 문자만으로 실제 기관인지는 확인할 수 없어요.",
    EvidenceCode.ACQUAINTANCE_CLAIM: "보낸 사람이 아는 사람이라고 했지만, 문자만으로 본인인지는 확인할 수 없어요.",
    EvidenceCode.USER_NOT_PERSON: "원래 알던 번호로 통화해 보니 본인이 아니라고 답해 주셨어요.",
    EvidenceCode.USER_IS_PERSON: "원래 알던 번호로 통화해 보니 본인이 맞다고 답해 주셨어요.",
}
# 통화용 문장의 "상대가" 를 문자에 맞게 "보낸 사람이" 로 쓴다
MESSAGE_TEMPLATES = {
    code: text.replace("상대가", "보낸 사람이") for code, text in MESSAGE_TEMPLATES.items()
}

# 링크가 없어서 "스미싱" 이라고 쓰지 않는다 (FE D14)
MESSAGE_LEADS: dict[Conclusion, str] = {
    Conclusion.SUSPECTED: "사칭 문자로 의심돼요.",
    Conclusion.INCONCLUSIVE: "판단하기 어려워요.",
    Conclusion.INPUT_REQUIRED: "문자에서 내세운 기관·관계나 요구를 찾지 못했어요.",
    Conclusion.NOT_ANALYZABLE: "문자 내용을 살펴보지 못했어요.",
    Conclusion.OFFICIAL_DOMAIN: "",
}


def explain_message(conclusion: Conclusion, evidence: list[Evidence]) -> str:
    """링크 없는 문자용. explain_call 과 같은 방식으로 결론 한 줄과 근거 문장을 잇는다."""
    codes = {e.code for e in evidence}
    lines = [MESSAGE_LEADS[conclusion]]
    lines += [t for c, t in MESSAGE_TEMPLATES.items() if c in codes]
    return " ".join(line for line in lines if line)
