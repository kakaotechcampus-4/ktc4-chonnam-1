"""격리 서버 수집 API — `POST /collect`.

메인 서버(`scanner/isolation_client.py`, `scanner/collect_response.py`)와의 계약:

- 요청: HTTPS, `Authorization: Bearer <토큰>`, 본문 `{"url": "..."}` (다른 필드 없음)
- 200: `CollectResult` 필드 그대로의 JSON. 수집 실패도 200으로 돌려주고 `failures`에 남긴다
- 400 잘못된 요청 / 401 인증 실패 / 413 요청 본문 초과 / 503 수집 중(동시 실행 상한)

판정은 하지 않는다. 격리 서버의 비밀값은 수집 토큰과 TLS 개인키뿐이고, 둘 다
환경변수가 아니라 파일에서 읽는다. 나중에 브라우저를 붙일 때 환경변수가 그대로
넘어가도 토큰이 따라가지 않게 하기 위해서다 (docs/isolation-security.md §5.1).
"""

import asyncio
import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from collector.policy import MAX_URL_LENGTH, is_allowed_url, load_allowed_hosts
from scanner.fetch import collect_url
from scanner.models import CollectResult

MAX_REQUEST_BYTES = 4096
# 메인 서버는 256 KiB를 넘는 응답을 버린다. JSON 이스케이프로 커지는 몫을 두고 줄인다.
MAX_RESPONSE_BYTES = 240 * 1024
# 메인 서버 타임아웃(15초)보다 짧아야 실패 이유가 메인까지 전달된다.
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TITLE_CHARS = 300
MIN_TOKEN_LENGTH = 32

Collect = Callable[..., Awaitable[CollectResult]]

log = logging.getLogger("collector")


def _failed(url: str, failure: str) -> CollectResult:
    return CollectResult(
        input_url=url, final_url=None, redirect_chain=(), status_code=None,
        content_type=None, html="", title=None, elapsed_ms=0, failures=(failure,),
    )


def encode_result(result: CollectResult, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """응답이 `limit`을 넘지 않을 때까지 html을 절반씩 줄이고 `html_truncated`를 남긴다.

    html이 128 KiB 이하여도 제어 문자·따옴표는 JSON에서 2~6배로 커질 수 있다.
    """
    title = result.title[:MAX_TITLE_CHARS] if result.title else result.title
    html = result.html
    failures = list(result.failures)
    while True:
        payload = asdict(replace(result, title=title, html=html, failures=tuple(failures)))
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) <= limit or not html:
            return body
        html = html[: len(html) // 2]
        if "html_truncated" not in failures:
            failures.append("html_truncated")


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
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        return None
    return url


def create_app(
    *,
    token: str,
    allowed_hosts: frozenset[str],
    max_concurrency: int = 1,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    collect: Collect = collect_url,
) -> Starlette:
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError("수집 토큰이 너무 짧습니다")
    if not allowed_hosts:
        raise RuntimeError("허용 호스트가 비어 있습니다. 카테캠 1단계는 허용 목록 없이 실행하지 않습니다")

    expected_auth = f"Bearer {token}".encode()
    active = 0

    def url_allowed(url: str) -> bool:
        return is_allowed_url(url, allowed_hosts)

    async def collect_endpoint(request: Request) -> Response:
        nonlocal active

        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected_auth):
            log.warning("event=auth_rejected")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        body = await _read_body(request)
        if body is None:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        url = _parse_url(body)
        if url is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        if not url_allowed(url):
            result = _failed(url, "blocked_address")
        elif active >= max_concurrency:
            log.warning("event=busy host=%s", _host_for_log(url))
            return JSONResponse({"error": "busy"}, status_code=503, headers={"Retry-After": "1"})
        else:
            active += 1
            try:
                # collect_url은 timeout 안에 스스로 끝나지만, 예상 못 한 지연에 대비해 한 번 더 자른다.
                result = await asyncio.wait_for(
                    collect(url, timeout=timeout, url_allowed=url_allowed), timeout + 1.0
                )
            except TimeoutError:
                result = _failed(url, "timeout")
            except Exception:
                log.exception("event=collector_error host=%s", _host_for_log(url))
                result = _failed(url, "collector_error")
            finally:
                active -= 1

        log.info(
            "event=collect host=%s status=%s failures=%s elapsed_ms=%d",
            _host_for_log(url), result.status_code, ",".join(result.failures) or "-",
            result.elapsed_ms,
        )
        return Response(encode_result(result), media_type="application/json")

    return Starlette(routes=[Route("/collect", collect_endpoint, methods=["POST"])])


def create_app_from_env() -> Starlette:
    """uvicorn `--factory` 진입점. 토큰은 파일, 나머지는 환경변수에서 읽는다."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token_file = os.getenv("COLLECTOR_TOKEN_FILE", "/run/secrets/collect_token")
    return create_app(
        token=Path(token_file).read_text(encoding="utf-8").strip(),
        allowed_hosts=load_allowed_hosts(),
        max_concurrency=int(os.getenv("COLLECTOR_MAX_CONCURRENCY", "1")),
        timeout=float(os.getenv("COLLECTOR_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
    )
