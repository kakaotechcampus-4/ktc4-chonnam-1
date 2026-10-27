"""Q1 답 → 피해 대응 (v3 §7.7). 통화에 해당하는 줄만 둔다.

점검 순서가 곧 출력 순서다. 고른 순서가 아니라 표 순서라서, 시간이 급한
지급정지·상품권 정지가 늘 앞에 온다.
"""

from __future__ import annotations

from ai.case.types import Evidence, EvidenceCode, ResponseAction

_R = ResponseAction


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
            out += [_R.REGISTER_INFO_EXPOSURE, _R.CALL_1332]
        else:
            # 어떤 항목인지 모르면 더 넓은 대응을 준다
            out += [_R.CHANGE_PASSWORD, _R.CALL_1332]
    if "installed_app" in given:
        # 통화는 원격 조종·송금 유도가 흔해서 지급정지를 함께 붙인다
        out += [_R.REMOVE_APP_AND_CHECK_DEVICE, _R.REQUEST_PAYMENT_STOP]
    return list(dict.fromkeys(out))


def default_response_actions(option_id: str) -> list[ResponseAction]:
    """케이스를 찾지 못했을 때 쓴다 (v3 §7.7 마지막 표)."""
    if option_id == "sent_money":
        return [_R.REQUEST_PAYMENT_STOP, _R.CONTACT_GIFT_CARD_ISSUER, _R.REPORT_112]
    if option_id == "entered_info":
        return [_R.CHANGE_PASSWORD, _R.CALL_1332]
    if option_id == "installed_app":
        return [_R.REMOVE_APP_AND_CHECK_DEVICE, _R.REQUEST_PAYMENT_STOP]
    return []
