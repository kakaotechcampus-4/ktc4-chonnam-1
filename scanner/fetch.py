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
import dataclasses
import ipaddress
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from scanner.models import CollectResult

# failures 에 들어가는 값: invalid_scheme, invalid_url, blocked_address,
# redirect_limit, timeout, tls_cert_verify_failed, connection_failed,
# html_truncated·compressed_response_skipped(성공 응답에 덧붙는 경고성
# 값, 위 값들과 성격이 다르다).
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
    # 압축 해제 폭탄 방지(멘토 리뷰, PR #54): 서버가 그래도 압축해서 보내면
    # Content-Encoding 을 보고 본문을 아예 읽지 않는다 — 요청 헤더만으로는
    # 악성 서버가 무시할 수 있어 강제하지 않는다.
    "Accept-Encoding": "identity",
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


def _is_cert_verify_failure(exc: BaseException) -> bool:
    """만료·자체서명·호스트명 불일치 등 TLS **인증서 검증** 실패인지 판별한다.

    `ssl.SSLError`가 아니라 더 좁은 `ssl.SSLCertVerificationError`만 본다 —
    `SSLEOFError`/`SSLSyscallError`/`SSLZeroReturnError` 같은 형제 클래스는
    인증서와 무관한 TLS 오류인데, `ssl.SSLError`로 넓게 보면 이들도 인증서
    검증 실패로 잘못 분류되어 verify=False 재시도가 과도하게 일어난다
    (멘토 리뷰, PR #54).

    httpx 는 이 오류를 httpcore.ConnectError 로 감싸고, 실제
    ssl.SSLCertVerificationError 는 그 예외의 `args[0]`에 들어간다 —
    httpx/httpcore 의 공개 API가 아니라 버전에 따라 감싸는 깊이가 달라질 수
    있어 `__cause__`/`__context__` 체인을 몇 단계 따라가며 확인한다. 못
    찾아도 메시지 문자열(`CERTIFICATE_VERIFY_FAILED`)로 한 번 더 확인한다 —
    이 문자열 자체가 인증서 검증 실패에만 붙으므로 범위가 넓어지지 않는다.
    """
    node: BaseException | None = exc
    for _ in range(4):
        if node is None:
            break
        if isinstance(node, ssl.SSLCertVerificationError):
            return True
        if node.args and isinstance(node.args[0], ssl.SSLCertVerificationError):
            return True
        node = node.__cause__ or node.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


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


def _default_insecure_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=False)


async def collect_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    resolve: Resolver = default_resolve,
    max_redirects: int = MAX_REDIRECTS,
    timeout: float = FETCH_TIMEOUT_SECONDS,
    html_limit: int = HTML_LIMIT_BYTES,
    allow_insecure_retry: bool = True,
    make_insecure_client: Callable[[], httpx.AsyncClient] = _default_insecure_client,
    url_allowed: Callable[[str], bool] | None = None,
) -> CollectResult:
    """URL 하나를 방문해 리다이렉트·응답·HTML 을 수집한다.

    실패해도 예외를 올리지 않는다 — 확인하지 못한 것은 `failures` 에 남기고
    그때까지 모은 정보(redirect_chain 등)는 그대로 돌려준다. `client` 를
    넘기지 않으면 이 호출 동안만 쓰는 클라이언트를 만들고 끝에 닫는다.

    TLS 인증서 검증에 실패하면(`tls_cert_verify_failed`) `verify=False`로
    한 번 더 전체를 재시도한다 — 스미싱 페이지가 검증 안 되는 서버 뒤에
    redirect 체인을 숨겨둔 사례(link24.kr 등)가 있어, 검증 실패만으로
    포기하면 final_url 을 영영 못 얻는다. 재시도가 성공해도
    `tls_cert_verify_failed` 는 failures 에 그대로 남긴다 — 위험 신호로
    쓸 "검증이 실패했다는 사실"이지, 수집 성공 여부와는 별개다(점수 계산은
    scorer 몫). TLS 검증 실패 외의 다른 실패는 재시도하지 않는다 —
    `verify=False`의 영향 범위는 아직 조사 전이라 좁게 유지한다.
    재시도는 남은 시간 예산 안에서만 돈다(전체 상한은 `timeout`과 같다).

    `url_allowed`를 넘기면 첫 요청과 리다이렉트 홉마다 요청을 보내기 전에
    확인하고, 거부되면 `blocked_address`로 끝낸다. 격리 수집 API가 테스트용
    허용 목록을 강제할 때 쓴다(docs/isolation-security.md §5.2). 메인 서버의
    기존 호출은 넘기지 않으므로 동작이 바뀌지 않는다.
    """

    start = time.monotonic()
    deadline = start + timeout
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        result = await _collect(
            url, client, resolve, max_redirects, timeout, html_limit, start, url_allowed
        )
    finally:
        if owns_client:
            await client.aclose()

    if not allow_insecure_retry or result.failures != ("tls_cert_verify_failed",):
        return result

    remaining = max(deadline - time.monotonic(), 0.001)
    async with make_insecure_client() as insecure_client:
        retry = await _collect(
            url, insecure_client, resolve, max_redirects, remaining, html_limit, start,
            url_allowed,
        )

    return dataclasses.replace(retry, failures=("tls_cert_verify_failed", *retry.failures))


async def _collect(
    url: str,
    client: httpx.AsyncClient,
    resolve: Resolver,
    max_redirects: int,
    timeout: float,
    html_limit: int,
    start: float,
    url_allowed: Callable[[str], bool] | None = None,
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

            if url_allowed is not None and not url_allowed(current):
                return _result(
                    url, chain, final_url=current, elapsed_start=start,
                    failures=("blocked_address",),
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
                content_encoding = response.headers.get("content-encoding")
                status_code = response.status_code

                compressed = bool(content_encoding) and content_encoding.lower() != "identity"
                if compressed:
                    # httpx의 aiter_bytes()는 자동으로 압축을 푸는데, 작은
                    # 압축 응답이 거대한 평문으로 부풀 수 있다(gzip bomb) —
                    # 32KB → 32MB에서 최대 할당 약 81MB 확인(멘토 리뷰,
                    # PR #54). 본문을 아예 읽지 않고 사실만 남긴다.
                    html, truncated = "", False
                elif _is_text_like(content_type):
                    body, truncated = await asyncio.wait_for(
                        _read_limited(response, html_limit), remaining()
                    )
                    html = _decode(body, response.charset_encoding)
                else:
                    html, truncated = "", False
            finally:
                await response.aclose()

            failures: tuple[str, ...] = ()
            if compressed:
                failures = ("compressed_response_skipped",)
            elif truncated:
                failures = ("html_truncated",)
            return _result(
                url, chain, final_url=current, status_code=status_code,
                content_type=content_type, html=html, elapsed_start=start, failures=failures,
            )
    except (TimeoutError, httpx.TimeoutException):
        return _result(url, chain, final_url=current, elapsed_start=start, failures=("timeout",))
    except httpx.HTTPError as e:
        failure = "tls_cert_verify_failed" if _is_cert_verify_failure(e) else "connection_failed"
        return _result(url, chain, final_url=current, elapsed_start=start, failures=(failure,))

    return _result(
        url, chain, final_url=current, elapsed_start=start, failures=("redirect_limit",)
    )
