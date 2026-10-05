"""자체 URL Collector — httpx 로 의심 URL 을 직접 방문해 수집한다.

urlscan 이 대신 해주던 "URL 방문·수집"을 우리 코드로 옮기는 1차 작업이다
(오늘 작업 계획 §담당 B). 위험도 판정(`score`, `malicious`)은 여기서
만들지 않고, 이후 별도 scorer 단계에서 `CollectResult` 를 입력으로 받아
계산한다. 이 모듈은 수집만 한다.

지금 구현하지 않은 것 (TODO 로 남김, 추후 Task 로 분리):
- Playwright 기반 3단계 수집 (JS 렌더링 후 리다이렉트·폼 관찰)
- meta refresh 태그를 통한 리다이렉트 추적 (HTTP 3xx 만 따라간다)
- DNS 재바인딩(검사 시점과 요청 시점 사이에 주소가 바뀌는 공격) 방어 —
  수집기를 별도 격리 환경·egress 방화벽으로 배치하는 것이 최종 방어선이고,
  이번 1차 작업 범위가 아니다.
- redirect 체인 각 홉의 콘텐츠 타입/다운로드 여부 분류 (앱 설치 파일 등) —
  scorer 와 결합할 다음 단계에서 다룬다.
"""

import asyncio
import ipaddress
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from scanner.models import CollectResult

MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 10.0
HTML_LIMIT_BYTES = 131_072  # 128 KiB. 초과분은 버리고 failures 에 html_truncated 를 남긴다.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
TEXT_CONTENT_TYPES = ("text/", "application/xhtml+xml", "application/xml", "application/json")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

Resolver = Callable[[str], Awaitable[list[str]]]

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def default_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # 파싱 안 되는 주소는 안전하게 차단한다.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def address_blocked(host: str, resolve: Resolver) -> bool:
    """사설·루프백·링크로컬·메타데이터 주소면 True.

    DNS 실패는 여기서 차단하지 않는다 — 접속 자체가 실패하면 요청 단계에서
    connection_failed 로 남는다.
    """
    if not host or host == "localhost":
        return True
    if _is_ip_literal(host):
        return _is_blocked_ip(host)
    try:
        addresses = await resolve(host)
    except OSError:
        return False
    return any(_is_blocked_ip(address) for address in addresses)


def extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _is_text_like(content_type: str | None) -> bool:
    if not content_type:
        return False
    base = content_type.split(";")[0].strip().lower()
    return any(base.startswith(prefix) for prefix in TEXT_CONTENT_TYPES)


async def _read_limited(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            return bytes(body[:limit]), True
    return bytes(body), False


def _decode(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _result(
    input_url: str,
    chain: list[str],
    *,
    final_url: str | None = None,
    status_code: int | None = None,
    content_type: str | None = None,
    html: str = "",
    elapsed_start: float,
    failures: tuple[str, ...] = (),
) -> CollectResult:
    return CollectResult(
        input_url=input_url,
        final_url=final_url,
        redirect_chain=tuple(chain),
        status_code=status_code,
        content_type=content_type,
        html=html,
        title=extract_title(html) if html else None,
        elapsed_ms=int((time.monotonic() - elapsed_start) * 1000),
        failures=failures,
    )


async def collect_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    resolve: Resolver = default_resolve,
    max_redirects: int = MAX_REDIRECTS,
    timeout: float = FETCH_TIMEOUT_SECONDS,
    html_limit: int = HTML_LIMIT_BYTES,
) -> CollectResult:
    """URL 하나를 방문해 리다이렉트·응답·HTML 을 수집한다.

    실패해도 예외를 올리지 않는다 — 확인하지 못한 것은 `failures` 에 남기고
    그때까지 모은 정보(redirect_chain 등)는 그대로 돌려준다. `client` 를
    넘기지 않으면 이 호출 동안만 쓰는 클라이언트를 만들고 끝에 닫는다.
    """

    start = time.monotonic()
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        return await _collect(url, client, resolve, max_redirects, timeout, html_limit, start)
    finally:
        if owns_client:
            await client.aclose()


async def _collect(
    url: str,
    client: httpx.AsyncClient,
    resolve: Resolver,
    max_redirects: int,
    timeout: float,
    html_limit: int,
    start: float,
) -> CollectResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    def remaining() -> float:
        return max(deadline - loop.time(), 0.001)

    chain: list[str] = []
    current = url

    try:
        parsed = urlsplit(current)
    except ValueError:
        return _result(url, chain, elapsed_start=start, failures=("invalid_url",))

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _result(url, chain, elapsed_start=start, failures=("invalid_scheme",))

    try:
        for _ in range(max_redirects + 1):
            if deadline <= loop.time():
                return _result(
                    url, chain, final_url=current, elapsed_start=start, failures=("timeout",)
                )

            host = urlsplit(current).hostname
            if await asyncio.wait_for(address_blocked(host, resolve), remaining()):
                return _result(
                    url, chain, final_url=current, elapsed_start=start,
                    failures=("blocked_address",),
                )

            request = client.build_request(
                "GET", current, headers=DEFAULT_HEADERS, timeout=httpx.Timeout(remaining())
            )
            response = await asyncio.wait_for(
                client.send(request, stream=True, follow_redirects=False), remaining()
            )
            try:
                location = response.headers.get("location")
                if response.status_code in REDIRECT_STATUSES and location:
                    current = urljoin(current, location)
                    chain.append(current)
                    continue

                content_type = response.headers.get("content-type")
                status_code = response.status_code

                if _is_text_like(content_type):
                    body, truncated = await asyncio.wait_for(
                        _read_limited(response, html_limit), remaining()
                    )
                    html = _decode(body, response.charset_encoding)
                else:
                    html, truncated = "", False
            finally:
                await response.aclose()

            failures = ("html_truncated",) if truncated else ()
            return _result(
                url, chain, final_url=current, status_code=status_code,
                content_type=content_type, html=html, elapsed_start=start, failures=failures,
            )
    except (TimeoutError, httpx.TimeoutException):
        return _result(url, chain, final_url=current, elapsed_start=start, failures=("timeout",))
    except httpx.HTTPError:
        return _result(
            url, chain, final_url=current, elapsed_start=start, failures=("connection_failed",)
        )

    return _result(
        url, chain, final_url=current, elapsed_start=start, failures=("redirect_limit",)
    )
