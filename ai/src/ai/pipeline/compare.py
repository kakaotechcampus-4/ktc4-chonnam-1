"""문자 주장과 페이지 관측의 차이를 관측합니다. 판정하지 않습니다.

PRD 루프의 ④(주장·행동 대조)에 해당하는 최소 구현입니다. LLM 을 부르지
않고 외부 I/O 도 없습니다. ⑤(근거 보강 판단)가 무엇을 더 조회할지 고르려면
먼저 "무엇이 어긋났는가"가 값으로 있어야 하므로, 그 재료를 만듭니다.

`ai.pipeline.__all__` 에 넣지 않습니다. BE 와 합의한 공개 함수 네 개는
`test_revision_pipeline.py` 가 고정하고 있으며, 연동 계약이 정해지기 전까지
이 함수는 실험·테스트에서 모듈 경로로만 씁니다.
"""

from __future__ import annotations

from enum import Enum

from ai.types import Brand, EnvDoubt, EnvironmentPart, MessageDoubt, MessagePart, Topic


class Conflict(str, Enum):
    """두 분석이 서로 다른 값을 낸 지점입니다. **위험 신호가 아닙니다.**

    배송 조회를 안내한 문자와 로그인 폼이 있는 페이지의 차이는 설명의
    재료이지 악성의 근거가 아닙니다. 최종 `result` 는 `url.official` 이
    정하며 이 값은 관여하지 않습니다.
    """

    BRAND = "brand_conflict"
    TOPIC = "topic_conflict"
    DOUBT = "doubt_conflict"


# 같은 기업 계열의 서비스는 값이 달라도 어긋난 것으로 보지 않습니다.
# 값을 합치지는 않습니다. 두 part 의 brand 는 각자 그대로 남고, 비교할
# 때만 같은 계열로 취급합니다.
_BRAND_FAMILIES: tuple[frozenset[Brand], ...] = (
    frozenset({Brand.CJ_LOGISTICS, Brand.CJ_PARCEL, Brand.CJ_EXPRESS, Brand.CJ_SHOPPING}),
    frozenset({Brand.LOTTE_PARCEL, Brand.LOTTE_MALL}),
)

# 문자가 밝힌 목적에 대해, 페이지에 있어도 어긋나지 않는 요소입니다.
# [검토 필요] 실제 페이지 사례로 검증되지 않은 설계 후보입니다.
# 격리 환경 작업 목록의 "정상 로그인·결제·앱 다운로드·주소 입력·조회 화면
# 사례" 검증이 끝나면 이 표를 다시 맞춰야 합니다.
_CONSISTENT_ELEMENTS: dict[MessageDoubt, frozenset[EnvDoubt]] = {
    MessageDoubt.APP_INSTALL: frozenset({EnvDoubt.APP_LINK}),
    MessageDoubt.ADDRESS_EDIT: frozenset({EnvDoubt.ADDRESS_FORM}),
    MessageDoubt.ADDRESS_CHECK: frozenset({EnvDoubt.ADDRESS_FORM, EnvDoubt.PARCEL_WIDGET}),
    MessageDoubt.IDENTITY_CHECK: frozenset({EnvDoubt.LOGIN_FORM, EnvDoubt.PERSONAL_FORM}),
    MessageDoubt.DATA_INPUT: frozenset(
        {EnvDoubt.PERSONAL_FORM, EnvDoubt.LOGIN_FORM, EnvDoubt.ADDRESS_FORM}),
    MessageDoubt.PHOTO_VIEW: frozenset({EnvDoubt.DOCUMENT_VIEW}),
    MessageDoubt.PARCEL_LOOKUP: frozenset({EnvDoubt.PARCEL_WIDGET}),
    MessageDoubt.DETAIL_VIEW: frozenset({EnvDoubt.DOCUMENT_VIEW, EnvDoubt.PARCEL_WIDGET}),
    MessageDoubt.CANCEL_REFUND: frozenset({EnvDoubt.PAYMENT, EnvDoubt.LOGIN_FORM}),
    MessageDoubt.PICKUP: frozenset({EnvDoubt.PARCEL_WIDGET, EnvDoubt.DOCUMENT_VIEW}),
    MessageDoubt.WITHDRAW: frozenset({EnvDoubt.PAYMENT}),
}

# 목적이 특정되지 않았거나(링크 접속) 페이지와 무관한 요구(전화 응대)라서
# 페이지 요소와 맞대어 볼 수 없는 값입니다. 비교하지 않습니다.
_NOT_COMPARABLE = frozenset({MessageDoubt.OPEN_LINK, MessageDoubt.ANSWER_PHONE})


def _brand_conflict(message: Brand | None, env: Brand | None) -> bool:
    if message is None or env is None or Brand.UNKNOWN in (message, env):
        return False
    if message is env:
        return False
    return not any({message, env} <= family for family in _BRAND_FAMILIES)


def _topic_conflict(message: Topic | None, env: Topic | None) -> bool:
    if message is None or env is None or Topic.UNKNOWN in (message, env):
        return False
    return message is not env


def _doubt_conflict(message: MessageDoubt | None, env: EnvDoubt | None) -> bool:
    if message is None or env is None:
        return False
    if message in (MessageDoubt.UNKNOWN, MessageDoubt.NONE):
        return False
    if env in (EnvDoubt.UNKNOWN, EnvDoubt.NONE):
        return False
    if message in _NOT_COMPARABLE:
        return False
    return env not in _CONSISTENT_ELEMENTS.get(message, frozenset())


def compare_claims(message: MessagePart, env: EnvironmentPart) -> tuple[Conflict, ...]:
    """두 분석의 같은 이름 항목을 맞대어 어긋난 지점만 돌려줍니다.

    확인하지 못한 값은 어긋남으로 세지 않습니다. `unknown`, `없음`, 조기
    반환의 `None` 은 전부 "비교하지 않음"이며, 이것을 불일치로 처리하면
    수행하지 않은 확인을 주장하게 됩니다.

    어느 쪽의 값도 고치지 않습니다. 반환값은 차이의 목록일 뿐이고 두 part 는
    그대로 남습니다.
    """
    conflicts: list[Conflict] = []
    if _brand_conflict(message.brand, env.brand):
        conflicts.append(Conflict.BRAND)
    if _topic_conflict(message.category, env.category):
        conflicts.append(Conflict.TOPIC)
    if _doubt_conflict(message.details.doubt, env.details.doubt):
        conflicts.append(Conflict.DOUBT)
    return tuple(conflicts)
