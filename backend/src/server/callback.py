"""분석 결과를 카카오 callbackUrl 로 보낸다.

콜백 주소에는 1회용 토큰이 들어 있다. 주소와 응답 본문은 로그나 Job 에 남기지 않는다.
"""

from collections.abc import Callable

import httpx

from backend.src.server.callback_guard import is_allowed_callback_url
from backend.src.server.jobs import set_callback_status
from backend.src.server.log import get_logger

_log = get_logger("server")

# docs/latency-budget.md 의 "콜백 HTTP 전송"
CALLBACK_SEND_TIMEOUT_SECONDS = 10.0


def _safe_callback_error(error: Exception) -> str:
    """콜백 전송 실패를 예외 유형과 HTTP 상태 코드로만 요약한다.

    httpx 예외는 str() 에 요청 주소 전체(쿼리의 1회용 토큰 포함)를 담는다.
    """

    status_code = getattr(getattr(error, "response", None), "status_code", None)
    if status_code is not None:
        return f"{type(error).__name__} (status={status_code})"
    return type(error).__name__


def _result_status(response: httpx.Response) -> str | None:
    """카카오가 돌려준 status. JSON 이 아니면 None.

    응답이 요청 주소나 토큰을 status 에 그대로 되돌려 보내도 남기지 않도록 아는 값만 통과시킨다.
    """

    try:
        body = response.json()
    except ValueError:
        return None
    status = body.get("status") if isinstance(body, dict) else None
    return status if status in ("SUCCESS", "FAIL") else "UNKNOWN"


async def send_callback(
    get_client: Callable[[], httpx.AsyncClient], callback_url: str, payload: dict, job_id: str,
) -> None:
    """`payload` 를 콜백으로 보내고 성공 여부를 Job 에 남긴다. 예외를 올리지 않는다.

    실패해도 결과는 이미 Job 에 저장돼 있어 "결과 확인"으로 다시 받을 수 있다.
    """

    try:
        _log.info(f"[CALLBACK] job_id={job_id} 결과 전송 시작")

        # 입구에서 이미 걸렀지만, 다른 경로로 들어와도 내부 주소로 보내지 않게 한 번 더 본다.
        if not is_allowed_callback_url(callback_url):
            raise ValueError("허용되지 않은 callbackUrl")

        response = await get_client().post(
            callback_url, json=payload, timeout=CALLBACK_SEND_TIMEOUT_SECONDS, follow_redirects=False,
        )
        _log.info(f"[CALLBACK STATUS] job_id={job_id} {response.status_code}")
        if response.status_code >= 400:
            _log.warning(f"[CALLBACK ERROR] job_id={job_id} status={response.status_code}")
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001 — 전송 실패는 조회 폴백으로 넘긴다. 분석 작업을 죽이지 않는다
        safe_error = _safe_callback_error(error)
        set_callback_status(job_id, "failed", safe_error)
        _log.warning(f"[CALLBACK SEND FAILED] job_id={job_id} {safe_error}")
        _log.warning(f"[JOB CALLBACK FAILED] job_id={job_id}")
        return

    status = _result_status(response)
    if status is None:
        set_callback_status(job_id, "failed", "invalid_callback_response")
        _log.warning(f"[CALLBACK WARNING] job_id={job_id} 응답을 JSON으로 파싱할 수 없습니다.")
        _log.warning(f"[JOB CALLBACK FAILED] job_id={job_id}")
        return

    _log.info(f"[CALLBACK RESULT STATUS] job_id={job_id} status={status}")
    if status == "SUCCESS":
        set_callback_status(job_id, "success")
        _log.info(f"[JOB CALLBACK SUCCESS] job_id={job_id}")
    else:
        set_callback_status(job_id, "failed", f"kakao_callback_status={status}")
        _log.warning(f"[JOB CALLBACK FAILED] job_id={job_id} status={status}")
