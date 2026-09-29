"""공식 도메인 화이트리스트 대조 서비스.

`BE_DOMAIN_MATCH_TASK_GUIDE.md` 기준 구현. AI가 확인한 브랜드와 URL(또는
격리 환경)에서 확보한 도메인을 비교해 `DomainMatch`를 반환한다.

- 판정은 여기서 끝난다. AI의 message/env 분석, 자체 URL 점수기, FE 연결,
  전체 result 계산은 이 모듈의 책임이 아니다.
- 확인되지 않은 브랜드를 임의로 `brand_mismatch`로 판정하지 않는다 —
  대조할 정보 자체가 없으면 `not_registered`다.
"""

from typing import Literal

from services.official_domains import OFFICIAL_DOMAINS

DomainMatch = Literal[
    "official",
    "brand_mismatch",
    "not_registered",
    "unresolved",
]


def _normalize_domain(domain: str) -> str:
    """대소문자·앞뒤 공백·끝의 '.'을 정규화한다.

    이미 추출된 domain 문자열을 입력받는 구조이므로, URL에서 도메인을
    파싱하는 것까지는 이 함수의 책임이 아니다.
    """

    return domain.strip().strip(".").lower()


def is_same_or_subdomain(domain: str, official_domain: str) -> bool:
    """domain이 official_domain 자신이거나 그 서브도메인인지 확인한다.

    단순 `official_domain in domain` 포함 검사는 쓰지 않는다 —
    "naver.com.attacker.com"처럼 공식 도메인 문자열을 포함하지만
    실제로는 다른 도메인인 위장 주소를 막기 위해서다.
    """

    domain = _normalize_domain(domain)
    official_domain = _normalize_domain(official_domain)

    return (
        domain == official_domain
        or domain.endswith("." + official_domain)
    )


def check_official_domain(
    brand: str | None,
    domain: str | None,
) -> DomainMatch:
    """브랜드가 주장하는 도메인이 화이트리스트와 일치하는지 판정한다.

    판정 순서:
        1. domain이 없거나 빈 문자열/공백뿐이면 대조 자체가 불가능 → unresolved
        2. brand가 없거나 화이트리스트에 등록되지 않았으면 → not_registered
        3. 등록된 공식 도메인과 비교해 일치하면 official, 아니면 brand_mismatch
    """

    if domain is None or not domain.strip():
        return "unresolved"

    if brand is None:
        return "not_registered"

    official_domains = OFFICIAL_DOMAINS.get(brand)

    if not official_domains:
        return "not_registered"

    matched = any(
        is_same_or_subdomain(domain, official_domain)
        for official_domain in official_domains
    )

    return "official" if matched else "brand_mismatch"
