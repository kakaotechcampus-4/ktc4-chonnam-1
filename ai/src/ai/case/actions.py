"""Q1 답 → 피해 대응 (v3 §7.7). 통화에 해당하는 줄만 둔다.

값은 FE D17(`chatbot-copy.md` 3절 "v3 대응 표(§7.7)에 바라는 변경")을 따른다.
`call_1332` 를 빼고, 앱 설치에는 금감원 대처 안내 순서(전원 끄기 → 다른 기기 → 앱 삭제)를 쓴다.

점검 순서가 곧 출력 순서다. 고른 순서가 아니라 표 순서라서, 시간이 급한
지급정지·상품권 정지가 늘 앞에 온다.
"""

from __future__ import annotations

from ai.case.types import Evidence, EvidenceCode, ResponseAction, VoiceCaseResult

_R = ResponseAction

# 통화는 원격 조종·송금 유도가 흔해서 지급정지를 맨 앞에 붙인다
_INSTALLED_APP = [
    _R.REQUEST_PAYMENT_STOP,
    _R.ISOLATE_PHONE,
    _R.USE_OTHER_DEVICE,
    _R.REMOVE_APP_AND_CHECK_DEVICE,
]


def response_actions_for(
    answers: list[str], evidence: list[Evidence]
) -> list[ResponseAction]:
    """쌓인 Q1 답마다 표를 적용해 합친다. not_yet·unknown 은 아무것도 더하지 않는다."""
    given = set(answers)
    codes = {e.code for e in evidence}
    info_quotes = [
        e.quote or "" for e in evidence if e.code == EvidenceCode.INFO_REQUEST
    ]
    out: list[ResponseAction] = []

    if "sent_money" in given:
        if EvidenceCode.GIFT_CARD_REQUEST in codes:
            out += [_R.CONTACT_GIFT_CARD_ISSUER, _R.REPORT_112]
        else:
            out += [_R.REQUEST_PAYMENT_STOP, _R.REPORT_112]
    if "entered_info" in given:
        if any("주민" in q for q in info_quotes):
            out += [_R.REGISTER_INFO_EXPOSURE]
        else:
            # 어떤 항목인지 모르면 더 넓은 대응을 준다
            out += [_R.CHANGE_PASSWORD, _R.REGISTER_INFO_EXPOSURE]
    if "installed_app" in given:
        out += _INSTALLED_APP
    return list(dict.fromkeys(out))


def default_response_actions(option_id: str) -> list[ResponseAction]:
    """케이스를 찾지 못했을 때 쓴다 (v3 §7.7 마지막 표)."""
    if option_id == "sent_money":
        return [_R.REQUEST_PAYMENT_STOP, _R.CONTACT_GIFT_CARD_ISSUER, _R.REPORT_112]
    if option_id == "entered_info":
        return [_R.CHANGE_PASSWORD, _R.REGISTER_INFO_EXPOSURE]
    if option_id == "installed_app":
        return list(_INSTALLED_APP)
    return []


def apply_call_answers(result: VoiceCaseResult, answers: list[str]) -> VoiceCaseResult:
    """쌓인 Q1 답으로 `response_actions` 를 다시 정한 사본을 돌려준다 (v3 §8, 통화는 Q1 만).

    결론·caution·멈출 행동은 그대로다. LLM·네트워크를 쓰지 않는다. 표에 없는 답은
    아무것도 더하지 않는다. Q1 이 붙지 않은 결과에 온 답도 대응을 고른다(v3 §8).
    """
    return result.model_copy(
        update={"response_actions": response_actions_for(answers, result.evidence)}
    )
