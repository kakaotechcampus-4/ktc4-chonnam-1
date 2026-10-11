"""단축 주소 뒤의 실제 주소 찾기.

문자 속 링크가 bit.ly 같은 단축 주소면, 그 도메인으로는 공식 여부를 판정할 수 없다.
그렇다고 urlscan 의 최종 도메인을 쓰면 "단축 주소 → 공격자 주소 → (스캐너에게만)
공식 사이트" 경로에서 공격자 주소를 공식으로 안내하게 된다.

그래서 리다이렉트 경로에서 단축 도메인만 걷어내고 **처음 나오는 실제 도메인**으로
판정한다. 단축 서비스는 누가 열어도 같은 곳으로 보내므로 믿을 수 있고, 클로킹은
그 뒤의 공격자 서버에서 일어나므로 첫 실제 도메인은 클로킹의 영향을 받지 않는다.

경로는 격리 서버의 2단계 수집으로 얻는다. 메인 서버는 사용자 링크에 접속하지 않는다
(docs/isolation-security.md §5.7). 격리 서버는 허용 목록 밖 주소가 나오면 요청을 보내지
않고 그 주소를 경로에 기록만 하므로, 단축 도메인만 허용해 두면 공격자 사이트에는
접속하지 않고 첫 실제 주소를 얻는다.

경로를 얻지 못하면(격리 서버 미설정·실패, 단축 서비스가 리다이렉트 대신 안내 페이지를
주는 경우) None 을 돌려주고, 호출하는 쪽은 판단 보류(unresolved)로 둔다.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from scanner.models import CollectResult

log = logging.getLogger(__name__)

# 여러 기관이 함께 쓰는 범용 단축 도메인. 기관이 직접 운영하는 짧은 도메인
# (coupa.ng, gmkt.kr 등)은 여기가 아니라 공식 도메인 목록(services/official_domains.py)에
# 넣는다. 격리 서버의 허용 목록(infra/isolation/egress-hosts)과 함께 관리한다 —
# 여기만 있고 격리 서버에 없으면 경로를 얻지 못해 판단 보류가 된다.
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

Collect = Callable[[str], Awaitable[CollectResult]]


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


async def resolve_real_host(link: str, collect: Collect | None = None) -> str | None:
    """격리 서버로 단축 주소를 풀어 첫 실제 호스트를 돌려준다. 실패하면 None."""
    if collect is None:
        from scanner.isolation_client import collect_url_isolated as collect
    try:
        result = await collect(link)
    except Exception as exc:  # noqa: BLE001 — 실패는 판단 보류로 이어진다
        log.warning("shortener resolve failed: %s", type(exc).__name__)
        return None
    chain = list(result.redirect_chain)
    if result.final_url and (not chain or chain[-1] != result.final_url):
        chain.append(result.final_url)
    return first_real_host(link, chain)
