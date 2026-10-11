
"""격리환경 /collect API 호출 클라이언트.

이 모듈은 HTTP 통신과 HTTP 오류 처리만 담당한다.
응답 스키마 검증 및 CollectResult 변환은 별도 파서에 위임한다.
"""

import json
import os
import ssl
from collections.abc import Callable

import httpx

from scanner.models import CollectResult

from scanner.collect_response import parse_collect_response


ISOLATION_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 524_288  # 512 KiB: 2단계 HTML 128 KiB + 3단계 HTML 128 KiB + JSON 여유


class IsolationClientError(Exception):
    """격리환경 API 호출 또는 응답 수신 실패."""


def _tls_verify() -> ssl.SSLContext | bool:
    """격리 서버의 자체 서명 인증서를 고정한다 (docs/isolation-security.md §5.5).

    `ISOLATION_API_CA_CERT`(PEM 원문)가 있으면 그 인증서 하나만 믿는다.
    없으면 기존처럼 시스템 CA로 검증한다. 어느 쪽이든 검증을 끄지 않는다.
    """
    pem = os.getenv("ISOLATION_API_CA_CERT", "").strip()
    if not pem:
        return True
    try:
        return ssl.create_default_context(cadata=pem)
    except ssl.SSLError as exc:
        raise IsolationClientError("격리환경 API 인증서 설정이 올바르지 않습니다.") from exc


async def collect_url_isolated(
    url: str,
    *,
    parse_response: Callable[[str, dict], CollectResult] = parse_collect_response,
    client: httpx.AsyncClient | None = None,
) -> CollectResult:
    api_url = os.getenv("ISOLATION_API_URL", "").strip()
    token = os.getenv("ISOLATION_API_TOKEN", "").strip()

    if not api_url or not token:
        raise IsolationClientError(
            "격리환경 API 주소 또는 인증 토큰이 설정되지 않았습니다."
        )

    # 잘못된 설정으로 토큰이 평문 전송되지 않도록 제한한다.
    if not api_url.startswith("https://"):
        raise IsolationClientError(
            "격리환경 API는 HTTPS 주소를 사용해야 합니다."
        )

    endpoint = api_url.rstrip("/") + "/collect"
    owns_client = client is None

    if owns_client:
        client = httpx.AsyncClient(
            timeout=ISOLATION_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            verify=_tls_verify(),
        )

    try:
        try:
            async with client.stream(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"url": url},
            ) as response:
                response.raise_for_status()

                chunks = []
                total_bytes = 0

                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)

                    if total_bytes > MAX_RESPONSE_BYTES:
                        raise IsolationClientError(
                            "격리환경 API 응답 크기 초과"
                        )

                    chunks.append(chunk)

                raw_body = b"".join(chunks)

        except httpx.TimeoutException as exc:
            raise IsolationClientError(
                "격리환경 API 요청 시간 초과"
            ) from exc

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise IsolationClientError(
                f"격리환경 API HTTP 오류: {status}"
            ) from exc

        except httpx.RequestError as exc:
            raise IsolationClientError(
                f"격리환경 API 연결 실패: {type(exc).__name__}"
            ) from exc

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IsolationClientError(
                "격리환경 API 응답이 유효한 UTF-8 JSON이 아닙니다."
            ) from exc

        if not isinstance(data, dict):
            raise IsolationClientError(
                "격리환경 API 응답은 JSON 객체여야 합니다."
            )

        # 필드별 검증과 CollectResult 변환은 BE 팀원 담당.
        return parse_response(url, data)

    finally:
        if owns_client:
            await client.aclose()
