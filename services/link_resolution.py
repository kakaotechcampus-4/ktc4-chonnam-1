"""Find the first non-shortener host from the one shared collection result."""

from collections.abc import Iterable
from urllib.parse import urlsplit


SHORTENER_HOSTS: frozenset[str] = frozenset({
    "bit.ly",
    "naver.me",
    "me2.do",
    "han.gl",
    "url.kr",
    "vo.la",
    "kko.to",
    "tinyurl.com",
    "is.gd",
})

def _host(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".") if host else None


def is_shortener(host: str | None) -> bool:
    return bool(host) and host.lower().rstrip(".") in SHORTENER_HOSTS


def first_real_host(input_url: str, redirect_chain: Iterable[str]) -> str | None:
    """입력 주소와 리다이렉트 경로에서 단축 도메인이 아닌 첫 호스트.

    중간에 해석할 수 없는 주소가 있거나 끝까지 단축 도메인뿐이면 None.
    """
    for url in (input_url, *redirect_chain):
        host = _host(url)
        if host is None:
            return None
        if not is_shortener(host):
            return host
    return None
