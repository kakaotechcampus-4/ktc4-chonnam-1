"""기관 대표번호 대조 (v3 §6.3·§6.5, 스펙 D4).

번호가 공식인지는 로컬 KB 와의 대조로만 정한다. status 가 curated 인 기관만 쓴다.
KB 에 없는 기관은 "공식이 아니다" 가 아니라 "대조할 수 없다"(org_not_listed)다.
"""

import logging
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ai.case.types import NumberCheck

LOGGER = logging.getLogger(__name__)

NUMBERS_PATH = Path(__file__).resolve().parent / "official_numbers.yaml"
_LOAD_LOCK = threading.Lock()

_NON_DIGIT_RE = re.compile(r"\D")
# +82 뒤의 "(0)" 은 국내 표기 0 을 괄호로 적은 것이다
_INTL_RE = re.compile(r"^\+\s*82[\s-]*(?:\(0\)[\s-]*)?")

# 앞뒤가 숫자·쉼표인 덩어리(금액·날짜)는 잘라 내지 않는다
_LONG_RE = re.compile(
    r"(?<![\d,])(?:\+82[ -]?(?:\(0\)[ -]?)?)?\(?\d{1,4}\)?[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"
)
_SERVICE_RE = re.compile(r"(?<![\d,])1[5-8]\d{2}[-. ]?\d{4}(?!\d)")
# 3~4자리 특수번호는 금액·연도와 섞이기 쉬워 단위가 바로 붙으면 뺀다
_SHORT_RE = re.compile(
    r"(?<![\d,.-])1\d{2,3}(?!\d|[,.]\d|번째|[원년월일만천억%명개건분초시세살회])"
)
# 3~4자리는 번호 단서(앞뒤 6자 안)가 있거나 KB 에 있는 번호일 때만 전화번호로 본다
_PHONE_CUE_RE = re.compile(r"번|번호|전화|문의|ARS", re.IGNORECASE)
_CUE_WINDOW = 6
_CANDIDATE_RE = re.compile("|".join(p.pattern for p in (_LONG_RE, _SERVICE_RE, _SHORT_RE)))


@dataclass(frozen=True)
class OfficialOrg:
    org: str
    aliases: tuple[str, ...]
    numbers: frozenset[str]
    source_url: str


def normalize_number(raw: str) -> str | None:
    """공백·하이픈·괄호를 지우고 +82 를 0 으로 바꾼 숫자열. 전화번호 모양이 아니면 None."""
    text = raw.strip()
    if _INTL_RE.match(text):
        text = "0" + _INTL_RE.sub("", text)
    digits = _NON_DIGIT_RE.sub("", text)
    size = len(digits)
    if size == 3 and digits[0] == "1":  # 112, 118, 182 …
        return digits
    if size == 4 and digits[0] == "1":  # 1332, 1394 …
        return digits
    if size == 8 and digits[:2] in {"15", "16", "18"}:  # 1588-0000 …
        return digits
    if 9 <= size <= 11 and digits[0] == "0":  # 지역번호·휴대전화
        return digits
    return None


def find_numbers(text: str) -> list[str]:
    """문장 속 전화번호 후보를 나온 순서대로, 중복 없이 정규형으로 돌려준다.

    "일삼삼이" 같은 한국어 숫자 읽기는 다루지 않는다.
    """
    found: list[str] = []
    known = _known_short_numbers(Path(NUMBERS_PATH))
    for match in _CANDIDATE_RE.finditer(text):
        number = normalize_number(match.group())
        if number is None or number in found:
            continue
        if len(number) <= 4 and number not in known:
            # "150 달러", "130 명" 같은 금액·수량을 전화번호로 읽지 않는다
            around = text[max(0, match.start() - _CUE_WINDOW) : match.start()] + text[
                match.end() : match.end() + _CUE_WINDOW
            ]
            if not _PHONE_CUE_RE.search(around):
                continue
        found.append(number)
    return found


@lru_cache(maxsize=8)
def _known_short_numbers(path: Path) -> frozenset[str]:
    """status 와 상관없이 KB 에 적힌 3~4자리 번호. 단서 없이도 번호로 읽는다."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return frozenset()
    return frozenset(
        n
        for entry in (data or {}).get("orgs") or []
        if isinstance(entry, dict)
        for n in (normalize_number(str(x)) for x in entry.get("numbers") or [])
        if n and len(n) <= 4
    )


def load_orgs(path: Path = NUMBERS_PATH) -> tuple[OfficialOrg, ...]:
    """status 가 curated 인 기관만 읽는다. 프로세스 수명 동안 캐시한다(kb/search.py 와 같다)."""
    with _LOAD_LOCK:
        return _load_orgs(Path(path))


@lru_cache(maxsize=8)
def _load_orgs(path: Path) -> tuple[OfficialOrg, ...]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning("official numbers load failed: %s", type(exc).__name__)
        return ()

    orgs = []
    for entry in (data or {}).get("orgs") or []:
        if not isinstance(entry, dict) or entry.get("status") != "curated":
            continue
        source_url = entry.get("source_url")
        name = entry.get("org")
        if not name or not source_url:
            continue
        numbers = frozenset(
            n for n in (normalize_number(str(x)) for x in entry.get("numbers") or []) if n
        )
        aliases = tuple(str(a) for a in entry.get("aliases") or [])
        orgs.append(OfficialOrg(str(name), (str(name), *aliases), numbers, str(source_url)))
    return tuple(orgs)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _find_org(name_or_quote: str, path: Path) -> OfficialOrg | None:
    haystack = _squash(name_or_quote)
    best: tuple[int, OfficialOrg] | None = None
    for org in load_orgs(path):
        for alias in org.aliases:
            squashed = _squash(alias)
            # 더 긴 별칭이 이긴다: "농협카드" 문장이 "농협" 으로 은행에 붙지 않게
            if squashed and squashed in haystack and (best is None or len(squashed) > best[0]):
                best = (len(squashed), org)
    return best[1] if best else None


def resolve_org(name_or_quote: str, *, path: Path = NUMBERS_PATH) -> str | None:
    """이름이나 문장에서 별칭이 걸리는 기관의 정식 이름. 없으면 None."""
    org = _find_org(name_or_quote, path)
    return org.org if org else None


def check_phone_number(number: str, org: str, *, path: Path = NUMBERS_PATH) -> NumberCheck:
    """번호가 org 의 공식 대표번호인지. org 는 별칭이어도 된다."""
    entry = _find_org(org, path)
    normalized = normalize_number(number) or number
    if entry is None:
        return NumberCheck(number=normalized, org=org, result="org_not_listed")
    result = "official" if normalized in entry.numbers else "not_official"
    return NumberCheck(
        number=normalized, org=entry.org, result=result, source_url=entry.source_url
    )
