"""카카오 음성 메시지 CDN URL 검사 (callback_guard.py와 같은 원칙).

`/kakao/skill`로 들어오는 `userRequest.params.media.url`은 카카오가 만든
서명된 URL이지만, 그 값 자체를 신뢰하지 않는다 — 콜백 URL과 같은 이유로
`http://169.254.169.254/...`, `http://localhost:...` 같은 내부 주소로
우리 서버가 요청을 보내게 만들 수 있으면 안 된다(멘토 리뷰, isolation-
security.md §5.5와 같은 원칙).

허용 조건은 콜백 URL 검사와 동일: https, 포트 443(생략 포함), 사용자
정보(`user@`) 없음, 호스트가 허용 목록(`KAKAO_MEDIA_HOSTS`, 쉼표 구분)에
정확히 있음. 미설정이면 모두 거부한다 — 실제 카카오 CDN 호스트는 운영
로그로 확인해 설정해야 하며 기본값을 추측하지 않는다.

콜백 URL 검사와 다른 점 하나: 허용 목록에 있는 호스트라도, 호출 시점에
DNS가 가리키는 주소를 다시 확인한다(`assert_resolves_to_public_ip`).
콜백 호스트는 카카오가 소유한 고정 도메인이라 호스트 일치만으로 충분하다고
봤지만, 음성 다운로드는 실제로 우리 서버가 그 주소에 접속해 파일까지
받아오므로, 허용한 호스트의 DNS가 그 순간 내부 주소로 바뀌어 있는
경우(DNS 리바인딩)까지 막는다. 완전한 해결책은 해석한 IP에 직접 접속하는
것이지만(httpx 커스텀 transport 필요), 지금은 "검사 직후 바로 접속"으로
공격 가능 시간을 최소화하는 수준까지만 한다 — 더 필요하면 후속 작업.
"""

import asyncio
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

MAX_MEDIA_URL_LENGTH = 2048

Resolver = Callable[[str], Awaitable[list[str]]]


class MediaDownloadBlocked(Exception):
    """허용되지 않은 음성 다운로드 주소 또는 다운로드 실패."""


def _allowed_media_hosts() -> frozenset[str]:
    raw = os.getenv("KAKAO_MEDIA_HOSTS", "")
    return frozenset(h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip())


def media_host(url: object) -> str | None:
    """로그용 호스트. 쿼리스트링(서명 토큰)은 절대 남기지 않는다."""
    if not isinstance(url, str):
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def is_allowed_media_url(url: object) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_MEDIA_URL_LENGTH:
        return False
    if "\\" in url or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc or port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return False
    return host in _allowed_media_hosts()


def is_public_ipv4(address: str) -> bool:
    """사설·루프백·링크로컬(169.254.0.0/16, 클라우드 메타데이터 포함)·
    예약·멀티캐스트 주소가 아닌 공인 IPv4인지 확인한다."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.version == 4
        and ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
    )


async def default_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM, family=socket.AF_INET,
    )
    return [info[4][0] for info in infos]


async def assert_resolves_to_public_ip(host: str, resolve: Resolver = default_resolve) -> None:
    """허용 목록에 있는 호스트라도 지금 이 순간의 DNS 응답을 다시 확인한다.

    해석된 주소가 하나도 없거나, 그중 하나라도 공인 IPv4가 아니면 전부
    거부한다 — 여러 주소 중 일부만 내부로 돌리는 방식까지 막기 위해서다.
    """
    try:
        addresses = await resolve(host)
    except OSError as error:
        raise MediaDownloadBlocked("DNS 조회 실패") from error
    if not addresses or any(not is_public_ipv4(address) for address in addresses):
        raise MediaDownloadBlocked("허용되지 않은 주소로 해석됨")
