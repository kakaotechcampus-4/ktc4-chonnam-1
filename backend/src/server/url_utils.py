import re
from urllib.parse import urlsplit

import tldextract


# Public Suffix List를 실행 중 네트워크에서 다운로드하지 않음
_tld_extract = tldextract.TLDExtract(suffix_list_urls=())

# Markdown 링크: [표시 문구](URL)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# http(s) URL 또는 스킴이 없는 도메인/경로
_URL_CANDIDATE = re.compile(
    r"(?<![\w@])"
    r"(?:https?://)?"
    r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z0-9-]+"
    r"(?::\d{1,5})?"
    r"(?:[/?#][^\s<>\[\]()]+)?",
    re.IGNORECASE,
)

_TRAILING_PUNCTUATION = ".,!?;:，。！？"


def _normalize_url(candidate: str) -> str | None:
    candidate = candidate.rstrip(_TRAILING_PUNCTUATION)

    if not candidate:
        return None

    url = (
        candidate
        if candidate.lower().startswith(("http://", "https://"))
        else f"https://{candidate}"
    )

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname

        if not hostname:
            return None

        extracted = _tld_extract(hostname)

        # 실제 등록 가능한 최상위 도메인인지 확인
        if not extracted.domain or not extracted.suffix:
            return None

        # IP 주소나 숫자/단위 표현의 오인식 방지
        if not re.search(r"[a-zA-Z]", extracted.suffix):
            return None

        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            return None

        return url
    except ValueError:
        return None


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
