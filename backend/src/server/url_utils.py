import re
import ipaddress
from urllib.parse import urlsplit

import tldextract


# Public Suffix List를 실행 중 네트워크에서 다운로드하지 않음
_tld_extract = tldextract.TLDExtract(suffix_list_urls=())

# Markdown 링크: [표시 문구](URL)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# 명시적 주소는 전체 토큰을 검증한다. 잘못된 포트/호스트를 유효한
# 접두 주소로 잘라 인식하지 않는다. 접속 허용 판단은 Collector 책임이다.
_EXPLICIT_URL_PATTERN = r"https?:[^\s<>()]*"
_EXPLICIT_URL = re.compile(_EXPLICIT_URL_PATTERN, re.IGNORECASE)
# 스킴 없는 입력만 PSL을 사용해 숫자/일반 문장 오인식을 줄인다.
_URL_CANDIDATE = re.compile(
    r"(?<![\w@])"
    r"(?:" + _EXPLICIT_URL_PATTERN + r"|"
    r"(?<![/:])"
    r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z0-9-]+"
    r"(?::\d{1,5})?"
    r"(?:[/?#][^\s<>\[\]()]*)?)",
    re.IGNORECASE,
)

_TRAILING_PUNCTUATION = ".,!?;:，。！？"


def _normalize_url(candidate: str) -> str | None:
    candidate = candidate.rstrip(_TRAILING_PUNCTUATION)

    if not candidate:
        return None

    explicit = bool(re.match(r"https?:", candidate, re.IGNORECASE))
    if explicit and not candidate.lower().startswith(("http://", "https://")):
        return None
    if "\\" in candidate or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in candidate):
        return None

    url = (
        candidate
        if candidate.lower().startswith(("http://", "https://"))
        else f"https://{candidate}"
    )

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname

        if not hostname or parsed.scheme.lower() not in {"http", "https"} or "@" in parsed.netloc:
            return None
        if parsed.netloc.endswith(":"):
            return None
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            return None

        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            ascii_host = hostname.rstrip(".").encode("idna").decode("ascii")
            if len(ascii_host) > 253 or not all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in ascii_host.split(".")
            ):
                return None
        if not explicit:
            extracted = _tld_extract(hostname)
            if (not extracted.domain or not extracted.suffix
                    or not re.search(r"[a-zA-Z]", extracted.suffix)):
                return None

        return url
    except (ValueError, UnicodeError):
        return None


def has_malformed_url(text: str) -> bool:
    """명시적 HTTP(S) 주소의 형식 오류. DNS·접속 가능 여부는 조회하지 않는다."""
    return any(_normalize_url(match.group()) is None for match in _EXPLICIT_URL.finditer(text))


def split_message(text: str):
    links = []

    def remove_markdown(match: re.Match) -> str:
        url = _normalize_url(match.group(2))
        if url:
            links.append(url)
            return ""
        return match.group(0)

    cleaned_text = _MARKDOWN_LINK.sub(remove_markdown, text)

    def remove_url(match: re.Match) -> str:
        candidate = match.group(0)
        url = _normalize_url(candidate)

        if url:
            links.append(url)
            return ""

        return candidate

    message = _URL_CANDIDATE.sub(remove_url, cleaned_text).strip()

    # 중복 URL 제거 (입력 순서 유지)
    links = list(dict.fromkeys(links))

    return links, message
