"""Shared callback HTTP client, owned by the serving event loop."""

from contextlib import asynccontextmanager

import httpx


_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def http_client_lifespan(app):
    """서버 수명 동안 쓸 httpx 클라이언트를 만들고 종료 시 닫는다.

    httpx.AsyncClient 생성은 SSL 초기화로 이벤트 루프를 200~470ms 막는다(측정).
    요청·폴링마다 만들지 않고, 서빙할 이벤트 루프 위에서 한 번만 만든다.
    클라이언트는 만든 루프에 묶이므로 import 시점에 만들지 않는다.
    """
    global _client
    _client = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        await _client.aclose()
        _client = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError(
            "HTTP 클라이언트가 없습니다. "
            "FastAPI(lifespan=http_client_lifespan) 로 앱을 만들었는지 확인하세요."
        )
    return _client
