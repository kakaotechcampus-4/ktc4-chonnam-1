import asyncio
import os
from contextlib import asynccontextmanager

import httpx


URLSCAN_API_KEY = os.getenv("URLSCAN_API_KEY")

URLSCAN_SCAN_URL = "https://urlscan.io/api/v1/scan"
URLSCAN_RESULT_URL = "https://urlscan.io/api/v1/result"

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


async def submit_url_scan(url: str) -> dict:
    if not URLSCAN_API_KEY:
        raise RuntimeError(
            "URLSCAN_API_KEY가 설정되어 있지 않습니다."
        )

    headers = {
        "API-Key": URLSCAN_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "url": url,
        "visibility": "public"
    }

    response = await get_http_client().post(
        URLSCAN_SCAN_URL,
        headers=headers,
        json=payload,
        timeout=10.0
    )

    print(f"[URLSCAN REQUEST] {payload}")
    print(f"[URLSCAN STATUS] {response.status_code}")
    print(f"[URLSCAN RESPONSE] {response.text}")

    response.raise_for_status()

    return response.json()

async def get_url_scan_result(scan_id: str) -> dict | None:
    """
    scan_id에 해당하는 검사 결과를 한 번 조회한다.

    검사 완료: 결과 JSON(dict) 반환
    검사 중: None 반환
    기타 오류: 예외 발생
    """

    if not URLSCAN_API_KEY:
        raise RuntimeError("URLSCAN_API_KEY가 설정되어 있지 않습니다.")

    url = f"{URLSCAN_RESULT_URL}/{scan_id}/"

    headers = {
        "API-Key": URLSCAN_API_KEY
    }

    response = await get_http_client().get(
        url,
        headers=headers,
        timeout=10.0
    )

    # 아직 urlscan 검사가 완료되지 않은 경우
    if response.status_code == 404:
        return None

    # 정상 결과가 아닌 경우 예외 발생
    response.raise_for_status()

    return response.json()


async def wait_for_url_scan_result(
    scan_id: str,
    max_attempts: int = 10,
    interval: int = 5
) -> dict | None:
    """
    urlscan 검사가 완료될 때까지 일정 간격으로 결과를 조회한다.

    기본값:
    - 최대 10번 조회
    - 조회 간격 5초
    - 최대 약 50초 대기

    검사 완료: 결과 JSON(dict) 반환
    시간 초과: None 반환
    """

    for attempt in range(max_attempts):

        result = await get_url_scan_result(scan_id)

        if result is not None:
            print(
                f"[URLSCAN RESULT] "
                f"scan_id={scan_id} completed"
            )

            return result

        print(
            f"[URLSCAN WAIT] "
            f"scan_id={scan_id} "
            f"attempt={attempt + 1}/{max_attempts}"
        )

        # 마지막 시도 후에는 기다릴 필요 없음
        if attempt < max_attempts - 1:
            await asyncio.sleep(interval)

    print(
        f"[URLSCAN TIMEOUT] "
        f"scan_id={scan_id}"
    )

    return None
