"""backend ↔ ai 사이에 오가는 타입. 호출당하는 쪽이 소유합니다."""

from dataclasses import dataclass
from enum import Enum


class ReasonCode(str, Enum):
    NO_URL = "no_url"
    OFFICIAL_MATCH = "official_match"
    LOOKALIKE = "lookalike"
    NOT_IN_WHITELIST = "not_in_whitelist"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Verdict:
    """백엔드가 판정을 끝낸 결과. ai는 이걸 문장으로 바꾸기만 합니다."""

    reason_code: ReasonCode
    url: str | None = None
    official_domain: str | None = None
    carrier_name: str | None = None
