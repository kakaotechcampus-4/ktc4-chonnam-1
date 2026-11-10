"""통화 게이트(`gate.py`)와 문자 게이트(`message_gate.py`)가 같이 쓰는 표.

두 게이트에 같은 표가 따로 있으면 한쪽만 고쳤을 때 통화와 문자 결과가 조용히 어긋난다.
게이트마다 다른 규칙(하한의 짝, "요구가 있다"의 범위, 멈출 행동을 고르는 순서)은 각 게이트에 둔다.
"""

from ai.case.types import EvidenceCode, StopAction
from ai.types import CaseSearchResult

_E = EvidenceCode

# 위험 요구. 주의(caution)와 Q1 을 붙일지 정할 때 본다
RISK_DEMANDS = frozenset(
    {
        _E.MONEY_REQUEST,
        _E.CASH_DELIVERY_REQUEST,
        _E.GIFT_CARD_REQUEST,
        _E.AUTH_CODE_REQUEST,
        _E.INFO_REQUEST,
        _E.APP_INSTALL_REQUEST,
        _E.REMOTE_APP_MENTION,
        _E.CALL_REQUEST,
    }
)

# 근거 코드 → 멈출 행동. 위에서부터 고른다. 링크 줄(dont_click_link)과 통화 끊기(hang_up)는
# 표에 없고 각 게이트가 앞뒤에 붙인다
STOP_TABLE: list[tuple[frozenset[EvidenceCode], StopAction]] = [
    (frozenset({_E.INFO_REQUEST}), StopAction.DONT_ENTER_INFO),
    (
        frozenset({_E.APP_INSTALL_REQUEST, _E.REMOTE_APP_MENTION}),
        StopAction.DONT_INSTALL_APP,
    ),
    (
        frozenset({_E.MONEY_REQUEST, _E.CASH_DELIVERY_REQUEST}),
        StopAction.DONT_SEND_MONEY,
    ),
    (frozenset({_E.GIFT_CARD_REQUEST}), StopAction.DONT_SEND_GIFT_CARD),
    (frozenset({_E.AUTH_CODE_REQUEST}), StopAction.DONT_SHARE_AUTH_CODE),
    (frozenset({_E.CALL_REQUEST}), StopAction.DONT_CALL_NUMBER_IN_MESSAGE),
]
# 결과에 싣는 멈출 행동의 최대 개수
MAX_STOP = 3

# Q1 "이미 하신 게 있나요?" 선택지. 링크가 없는 경로라 clicked_link 는 없다
Q1_OPTIONS = ["entered_info", "installed_app", "sent_money", "not_yet"]


def slim(similar: CaseSearchResult | None) -> CaseSearchResult | None:
    """matched_variant 는 사례 본문(통화는 대본 통째)이라 결과에 싣지 않는다. id·유사도·유형만 남긴다."""
    if similar is None:
        return None
    return CaseSearchResult(
        status=similar.status,
        matches=[m.model_copy(update={"matched_variant": ""}) for m in similar.matches],
    )
