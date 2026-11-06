"""격리 서버 수집 API — `POST /collect`.

메인 서버(`scanner/isolation_client.py`, `scanner/collect_response.py`)와의 계약:

- 요청: HTTPS, `Authorization: Bearer <토큰>`, 본문 `{"url": "..."}` (다른 필드 없음)
- 200: `CollectResult` 필드 그대로의 JSON. 수집 실패도 200으로 돌려주고 `failures`에 남긴다
- 400 잘못된 요청 / 401 인증 실패 / 413 요청 본문 초과 / 503 수집 중(동시 실행 상한)
- `browser`: 3단계를 돌리지 않았으면 null, 돌렸으면 `BrowserResult` 필드 (collector/browser.py)

판정은 하지 않는다. 격리 서버의 비밀값은 수집 토큰과 TLS 개인키뿐이고, 둘 다
환경변수가 아니라 파일에서 읽는다. 나중에 브라우저를 붙일 때 환경변수가 그대로
넘어가도 토큰이 따라가지 않게 하기 위해서다 (docs/isolation-security.md §5.1).
"""

import asyncio
import contextlib
import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from collector.policy import NetworkPolicy, parse_list
from collector.egress_proxy import EgressProxy
from scanner.fetch import collect_url
from scanner.models import MAX_TITLE_CHARS, MAX_URL_CHARS, BrowserResult, CollectResult

MAX_REQUEST_BYTES = 4096
# 메인 서버는 1 MiB를 넘는 응답을 버린다. JSON 이스케이프로 커지는 몫을 두고 줄인다.
MAX_RESPONSE_BYTES = 960 * 1024
# 2단계(1차·재시도) + 3단계 + 정리 여유가 메인 서버 타임아웃(17초)보다 짧아야 실패 이유가 메인까지 간다.
# 1차는 정상 응답이 거의 다 통과하는 시간이다. 멈춘 연결만 넉넉한 시간으로 한 번 더 받는다.
DEFAULT_FETCH_TIMEOUT_SECONDS = 2.0
DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS = 5.0
DEFAULT_BROWSER_TIMEOUT_SECONDS = 6.0
BACKSTOP_EXTRA_SECONDS = 3.0
BROWSER_MODES = ("off", "auto", "always")
MIN_TOKEN_LENGTH = 32

Collect = Callable[..., Awaitable[CollectResult]]


class Renderer(Protocol):
    async def render(self, url: str, *, trigger: str, timeout: float) -> BrowserResult: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def mark_broken(self) -> None: ...

log = logging.getLogger("collector")


def _failed(url: str, failure: str) -> CollectResult:
    return CollectResult(
        input_url=url, final_url=None, redirect_chain=(), status_code=None,
        content_type=None, html="", title=None, elapsed_ms=0, failures=(failure,),
    )


def encode_result(result: CollectResult, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """응답이 `limit`을 넘지 않을 때까지 더 긴 HTML 부터 절반씩 줄이고 `html_truncated`를 남긴다.

    HTML이 256 KiB 이하여도 제어 문자·따옴표는 JSON에서 2~6배로 커질 수 있다.
    """
    def short(title: str | None) -> str | None:
        return title[:MAX_TITLE_CHARS] if title else title

    def short_url(url: str | None) -> str | None:
        return url[:MAX_URL_CHARS] if url else url

    browser = result.browser
    if browser is not None:
        browser = replace(browser, title=short(browser.title), final_url=short_url(browser.final_url))
    # 리다이렉트 Location 은 2048자를 넘을 수 있다. HTML 을 다 비워도 상한을 넘지 않게 자른다.
    result = replace(
        result, title=short(result.title), final_url=short_url(result.final_url),
        redirect_chain=tuple(short_url(url) for url in result.redirect_chain), browser=browser,
    )

    while True:
        body = json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        browser = result.browser
        browser_len = len(browser.html) if browser else 0
        if len(body) <= limit or (not result.html and not browser_len):
            return body
        if browser is not None and browser_len >= len(result.html):
            failures = browser.failures if "html_truncated" in browser.failures else (*browser.failures, "html_truncated")
            result = replace(result, browser=replace(browser, html=browser.html[: browser_len // 2], failures=failures))
        else:
            failures = result.failures if "html_truncated" in result.failures else (*result.failures, "html_truncated")
            result = replace(result, html=result.html[: len(result.html) // 2], failures=failures)


def _host_for_log(url: str) -> str:
    # 경로·쿼리에는 수신자 토큰·전화번호가 들어 있을 수 있어 호스트만 남긴다 (§5.8).
    try:
        return urlsplit(url).hostname or "-"
    except ValueError:
        return "-"


async def _read_body(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_REQUEST_BYTES):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            return None
    return bytes(body)


def _parse_url(body: bytes) -> str | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or set(data) != {"url"}:
        return None
    url = data["url"]
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        return None
    return url


def create_app(
    *,
    token: str,
    network_policy: NetworkPolicy,
    max_concurrency: int = 1,
    fetch_timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    fetch_retry_timeout: float = DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS,
    browser_timeout: float = DEFAULT_BROWSER_TIMEOUT_SECONDS,
    browser_mode: str = "off",
    renderer: Renderer | None = None,
    trigger: Callable[[CollectResult], str | None] | None = None,
    collect: Collect = collect_url,
    egress_proxy: EgressProxy | None = None,
) -> Starlette:
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError("수집 토큰이 너무 짧습니다")
    policy = network_policy
    proxy = egress_proxy or EgressProxy(policy.host_allowed, connect_ip=policy.connect_ip)

    if browser_mode not in BROWSER_MODES:
        raise RuntimeError(f"COLLECTOR_BROWSER 는 {BROWSER_MODES} 중 하나여야 합니다")
    if browser_mode != "off" and (renderer is None or trigger is None):
        raise RuntimeError("브라우저 단계를 켜려면 renderer 와 trigger 가 필요합니다")

    expected_auth = f"Bearer {token}".encode()
    active = 0
    backstop = (fetch_timeout + fetch_retry_timeout
                + (browser_timeout if browser_mode != "off" else 0) + BACKSTOP_EXTRA_SECONDS)

    async def fetch_with_retry(url: str, **options) -> CollectResult:
        fetched = await collect(url, timeout=fetch_timeout, url_allowed=policy.url_allowed, **options)
        if fetched.failures != ("timeout",):
            return fetched
        log.info("event=fetch_retry host=%s", _host_for_log(url))
        return await collect(url, timeout=fetch_retry_timeout, url_allowed=policy.url_allowed, **options)

    async def run_stages(url: str) -> CollectResult:
        await proxy.start()
        # 두 단계 모두 이 CONNECT 프록시로만 나간다. 환경변수 프록시와 TLS 검증 끄기는 쓰지 않는다.
        async with httpx.AsyncClient(proxy=proxy.url, trust_env=False) as client:
            fetched = await fetch_with_retry(
                url, client=client, resolve=policy.resolve, allow_insecure_retry=False)
        if browser_mode == "off":
            return fetched
        reason = "always" if browser_mode == "always" else trigger(fetched)
        if reason is None:
            return fetched
        rendered = await renderer.render(url, trigger=reason, timeout=browser_timeout)
        return replace(fetched, browser=rendered)

    async def collect_endpoint(request: Request) -> Response:
        nonlocal active

        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected_auth):
            log.warning("event=auth_rejected peer=%s", request.client.host if request.client else "-")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        body = await _read_body(request)
        if body is None:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        url = _parse_url(body)
        if url is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        if not policy.url_allowed(url):
            result = _failed(url, "blocked_address")
        elif active >= max_concurrency:
            log.warning("event=busy host=%s", _host_for_log(url))
            return JSONResponse({"error": "busy"}, status_code=503, headers={"Retry-After": "1"})
        else:
            active += 1
            try:
                # 각 단계는 스스로 마감을 지키지만, 예상 못 한 지연에 대비해 한 번 더 자른다.
                result = await asyncio.wait_for(run_stages(url), backstop)
            except TimeoutError:
                if renderer is not None:
                    renderer.mark_broken()
                result = _failed(url, "timeout")
            except Exception:
                log.exception("event=collector_error host=%s", _host_for_log(url))
                result = _failed(url, "collector_error")
            finally:
                active -= 1

        browser = result.browser
        log.info(
            "event=collect host=%s status=%s failures=%s elapsed_ms=%d browser=%s "
            "browser_failures=%s blocked=%d downloads=%d",
            _host_for_log(url), result.status_code, ",".join(result.failures) or "-",
            result.elapsed_ms, browser.trigger if browser else "-",
            ",".join(browser.failures) if browser and browser.failures else "-",
            browser.blocked_requests if browser else 0, len(browser.downloads) if browser else 0,
        )
        return Response(encode_result(result), media_type="application/json")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await proxy.start()
        if renderer is not None and browser_mode != "off":
            await renderer.start()
        try:
            yield
        finally:
            if renderer is not None:
                await renderer.close()
            await proxy.close()

    return Starlette(routes=[Route("/collect", collect_endpoint, methods=["POST"])], lifespan=lifespan)


def create_app_from_env() -> Starlette:
    """uvicorn `--factory` 진입점. 토큰은 파일, 나머지는 환경변수에서 읽는다."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token_file = os.getenv("COLLECTOR_TOKEN_FILE", "/run/secrets/collect_token")
    policy = NetworkPolicy(
        mode=os.getenv("COLLECTOR_NETWORK_MODE", "allowlist"),
        allowed_hosts=parse_list(os.getenv("COLLECTOR_ALLOWED_HOSTS", "")),
        protected_hosts=parse_list(os.getenv("COLLECTOR_PROTECTED_HOSTS", "")),
        protected_ips=parse_list(os.getenv("COLLECTOR_PROTECTED_IPS", "")),
    )
    proxy = EgressProxy(policy.host_allowed, connect_ip=policy.connect_ip)
    browser_mode = os.getenv("COLLECTOR_BROWSER", "auto")
    renderer = trigger = None
    if browser_mode != "off":
        # playwright 는 브라우저를 켤 때만 불러온다. 운영 진입점은 샌드박스를 끄는 길이 없다.
        from collector.browser import BrowserRenderer, browser_trigger

        renderer = BrowserRenderer(
            policy.url_allowed,
            proxy,
            restart_every=int(os.getenv("COLLECTOR_BROWSER_RESTART_EVERY", "50")),
            sandbox=True,
        )
        trigger = browser_trigger
    return create_app(
        token=Path(token_file).read_text(encoding="utf-8").strip(),
        max_concurrency=int(os.getenv("COLLECTOR_MAX_CONCURRENCY", "1")),
        fetch_timeout=float(os.getenv("COLLECTOR_FETCH_TIMEOUT_SECONDS", str(DEFAULT_FETCH_TIMEOUT_SECONDS))),
        fetch_retry_timeout=float(
            os.getenv("COLLECTOR_FETCH_RETRY_TIMEOUT_SECONDS", str(DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS))),
        browser_timeout=float(os.getenv("COLLECTOR_BROWSER_TIMEOUT_SECONDS", str(DEFAULT_BROWSER_TIMEOUT_SECONDS))),
        browser_mode=browser_mode,
        renderer=renderer,
        trigger=trigger,
        network_policy=policy,
        egress_proxy=proxy,
    )
