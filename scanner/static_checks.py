"""1단계 정적 검사 — 접속 없이 도메인 문자열만 보고 확인한다.

docs/scanner-static-checks-proposal.md 기준 구현. 기존 공식 도메인 비교
(`services.official_domain_service.check_official_domain`)에 두 가지를
더한다.

- KISA 악성 도메인 목록 대조
- Punycode 디코딩 + Unicode 시각적 혼동(사칭) 분석

Punycode 사용 자체는 위험으로 보지 않는다 — 정상 국제화 도메인도 Punycode를
쓴다. 강한 신호로 보는 건 둘뿐이다: KISA 목록에 있거나, 디코딩한 도메인이
공식 도메인과 "시각적으로 혼동"될 때(실제로는 다른 도메인인데).

scanner/fetch.py 와 같은 원칙을 따른다 — 여기서는 사실만 돌려주고 위험
점수·판정은 하지 않는다. KISA 목록은 함수 호출부가 주입한다
(scanner/fetch.py 의 `resolve` 주입과 같은 이유: 아직 실제 데이터 연결
방법이 확정되지 않았고, 데이터가 없는 상태와 "확인했는데 없음"을
구분해야 한다 — `docs/scanner-static-checks-proposal.md` 4절).
"""

import unicodedata

import idna

from scanner.models import StaticCheckResult
from services.official_domain_service import check_official_domain, is_same_or_subdomain
from services.official_domains import OFFICIAL_DOMAINS

# Unicode 시각적 혼동 문자 → 라틴 알파벳. 공식 도메인이 전부 라틴 알파벳
# 문자열이라(services/official_domains.py), 자주 쓰이는 Cyrillic·Greek
# 동형이의 문자만 좁게 다룬다 — 넓은 커버리지가 필요해지면
# confusable_homoglyphs(PyPI, Unicode confusables.txt 기반) 전환을
# 검토한다(docs/scanner-static-checks-proposal.md 3절, 팀 확인 필요).
_CONFUSABLE_MAP: dict[str, str] = {
    # Cyrillic 소문자
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "ѕ": "s", "і": "i", "ј": "j", "ԁ": "d", "ѡ": "w", "ԛ": "q",
    # Cyrillic 대문자
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "Ѕ": "S", "Ј": "J",
    # Greek 소문자
    "α": "a", "ο": "o", "ρ": "p", "υ": "u", "ν": "v", "κ": "k", "τ": "t",
    "β": "b", "ι": "i",
    # Greek 대문자
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}


def _normalize(domain: str) -> str:
    return domain.strip().rstrip(".").lower()


def _is_punycode_domain(domain: str) -> bool:
    return any(label.lower().startswith("xn--") for label in domain.split("."))


def decode_domain(domain: str) -> tuple[str | None, bool]:
    """(디코딩 결과, Punycode 여부)를 돌려준다.

    Punycode 라벨이 없으면 (None, False) — 디코딩할 게 없다는 뜻이다.
    Punycode 라벨이 있는데 디코딩에 실패하면 (None, True) — Punycode는
    맞지만 내용을 확인 못 했다는 뜻이라 `failures`에 남긴다.
    """

    is_punycode = _is_punycode_domain(domain)
    if not is_punycode:
        return None, False

    try:
        return idna.decode(domain), True
    except (idna.IDNAError, UnicodeError, ValueError):
        return None, True


def skeleton(text: str) -> str:
    """시각적 혼동 비교용 정규화 문자열.

    NFKC로 호환 문자(전각·반각 등)를 먼저 통일한 뒤, 알려진 혼동 문자를
    라틴 알파벳으로 바꾸고 소문자로 맞춘다. 완전한 UTS#39 "skeleton"
    알고리즘이 아니라 좁은 범위의 근사치다(위 `_CONFUSABLE_MAP` 참고).
    """

    normalized = unicodedata.normalize("NFKC", text.strip().rstrip("."))
    return "".join(_CONFUSABLE_MAP.get(char, char) for char in normalized).lower()


def find_lookalike(
    domain: str,
    decoded_domain: str | None,
    *,
    whitelist: dict[str, set[str]] | None = None,
) -> str | None:
    """`domain`이 화이트리스트의 어느 공식 도메인과 시각적으로 혼동되는지 찾는다.

    실제 공식 도메인이거나 그 서브도메인이면(진짜 공식 주소) 제외한다 —
    "시각적으로 혼동되는 다른 도메인"만 의미가 있다. 브랜드를 가리지 않고
    전체 화이트리스트를 대상으로 본다 — 사칭은 AI가 식별한 브랜드와
    무관하게 아무 공식 도메인이나 흉내 낼 수 있다.
    """

    active_whitelist = OFFICIAL_DOMAINS if whitelist is None else whitelist
    compare_target = decoded_domain or domain
    target_skeleton = skeleton(compare_target)

    for official_domains in active_whitelist.values():
        for official_domain in official_domains:
            if is_same_or_subdomain(domain, official_domain):
                continue
            if skeleton(official_domain) == target_skeleton:
                return official_domain

    return None


def is_kisa_listed(
    domain: str, decoded_domain: str | None, kisa_domains: frozenset[str]
) -> bool:
    candidates = {_normalize(domain)}
    if decoded_domain:
        candidates.add(_normalize(decoded_domain))
    return bool(candidates & kisa_domains)


def check_static(
    domain: str | None,
    brand: str | None,
    *,
    whitelist: dict[str, set[str]] | None = None,
    kisa_domains: frozenset[str] | None = None,
) -> StaticCheckResult:
    """도메인 문자열만 보고 1단계 정적 검사를 수행한다 (접속 없음).

    `kisa_domains`를 생략하면 빈 목록으로 취급하고 `failures`에
    `kisa_feed_unavailable`을 남긴다 — "확인했는데 없음"과 "확인할 목록
    자체가 없음"을 구분한다(KISA 데이터 연결 방법은 아직 팀 확인 전,
    docs/scanner-static-checks-proposal.md 4절).
    """

    if domain is None or not domain.strip():
        return StaticCheckResult(
            domain=domain or "",
            official_match=check_official_domain(brand, domain, whitelist=whitelist),
            is_punycode=False,
            decoded_domain=None,
            kisa_listed=False,
            lookalike_of=None,
            failures=(),
        )

    official_match = check_official_domain(brand, domain, whitelist=whitelist)
    decoded_domain, is_punycode = decode_domain(domain)

    failures: list[str] = []

    if kisa_domains is None:
        kisa_listed = False
        failures.append("kisa_feed_unavailable")
    else:
        kisa_listed = is_kisa_listed(domain, decoded_domain, kisa_domains)

    lookalike_of = find_lookalike(domain, decoded_domain, whitelist=whitelist)

    return StaticCheckResult(
        domain=domain,
        official_match=official_match,
        is_punycode=is_punycode,
        decoded_domain=decoded_domain,
        kisa_listed=kisa_listed,
        lookalike_of=lookalike_of,
        failures=tuple(failures),
    )
