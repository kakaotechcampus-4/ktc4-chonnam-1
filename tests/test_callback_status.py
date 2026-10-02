"""run_analysis_and_callback의 callback 상태 저장 테스트.

실제 AI 분석, urlscan, 카카오 callback 서버는 호출하지 않는다.
run_analysis와 get_http_client를 mock하여 다음을 검증한다.

1. 분석 성공 + callback SUCCESS
2. 분석 성공 + callback FAIL
3. 분석 성공 + callback 응답 JSON 파싱 실패
4. 분석 성공 + callback HTTP 요청 자체 실패

분석 상태(status)와 callback 전달 상태(callback_status)가
서로 독립적으로 관리되는지도 함께 확인한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import main


@pytest.fixture(autouse=True)
def clean_jobs():
    """테스트마다 Job 저장소와 실행 중 사용자 상태를 초기화한다."""
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()

    yield

    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()


def _create_job(
    job_id: str,
    user_id: str = "test-user",
) -> None:
    """callback 테스트용 running Job을 생성한다."""
    main.ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "created_at": "2026-10-01T00:00:00+00:00",
        "completed_at": None,
        "result": None,
        "error": None,
        "callback_status": "pending",
        "callback_error": None,
    }

    main.RUNNING_USERS.add(user_id)


def _mock_http_client(
    monkeypatch,
    response,
) -> MagicMock:
    """지정한 response를 반환하는 가짜 HTTP client를 설정한다."""
    client = MagicMock()

    client.post = AsyncMock(
        return_value=response
    )

    monkeypatch.setattr(
        main,
        "get_http_client",
        lambda: client,
    )

    return client


@pytest.mark.asyncio
async def test_callback_success(monkeypatch):
    """분석과 callback이 모두 성공하면 각각 completed/success로 저장한다."""
    job_id = "job-callback-success"

    _create_job(job_id)

    analysis_result = main.kakao_response(
        "분석 완료"
    )

    monkeypatch.setattr(
        main,
        "run_analysis",
        AsyncMock(
            return_value=analysis_result
        ),
    )

    response = MagicMock()
    response.status_code = 200
    response.text = (
        '{"status":"SUCCESS"}'
    )
    response.json.return_value = {
        "status": "SUCCESS"
    }
    response.raise_for_status.return_value = None

    client = _mock_http_client(
        monkeypatch,
        response,
    )

    await main.run_analysis_and_callback(
        links=["https://example.com"],
        message="테스트 문자",
        callback_url="https://callback.example.com",
        user_id="test-user",
        job_id=job_id,
    )

    job = main.ANALYSIS_JOBS[job_id]

    assert job["status"] == "completed"
    assert job["result"] == analysis_result
    assert job["error"] is None

    assert job["callback_status"] == "success"
    assert job["callback_error"] is None

    # callback HTTP 요청이 실제로 한 번만 시도됐는지 확인
    client.post.assert_awaited_once_with(
        "https://callback.example.com",
        json=analysis_result,
        timeout=10.0,
    )

    # 작업이 끝났으므로 실행 중 사용자에서도 제거되어야 한다.
    assert "test-user" not in main.RUNNING_USERS


@pytest.mark.asyncio
async def test_callback_failure_status(monkeypatch):
    """HTTP 요청은 성공했지만 Kakao가 FAIL을 반환하면 callback 실패로 저장한다."""
    job_id = "job-callback-fail"

    _create_job(job_id)

    analysis_result = main.kakao_response(
        "분석 완료"
    )

    monkeypatch.setattr(
        main,
        "run_analysis",
        AsyncMock(
            return_value=analysis_result
        ),
    )

    response = MagicMock()
    response.status_code = 200
    response.text = (
        '{"status":"FAIL"}'
    )
    response.json.return_value = {
        "status": "FAIL"
    }
    response.raise_for_status.return_value = None

    _mock_http_client(
        monkeypatch,
        response,
    )

    await main.run_analysis_and_callback(
        links=["https://example.com"],
        message="테스트 문자",
        callback_url="https://callback.example.com",
        user_id="test-user",
        job_id=job_id,
    )

    job = main.ANALYSIS_JOBS[job_id]

    # 분석 자체는 성공했다.
    assert job["status"] == "completed"
    assert job["result"] == analysis_result
    assert job["error"] is None

    # 결과 전달만 실패했다.
    assert job["callback_status"] == "failed"
    assert (
        job["callback_error"]
        == "kakao_callback_status=FAIL"
    )

    assert "test-user" not in main.RUNNING_USERS


@pytest.mark.asyncio
async def test_callback_invalid_json_response(
    monkeypatch,
):
    """callback 응답이 JSON이 아니면 파싱 실패 원인을 저장한다."""
    job_id = "job-callback-invalid-json"

    _create_job(job_id)

    analysis_result = main.kakao_response(
        "분석 완료"
    )

    monkeypatch.setattr(
        main,
        "run_analysis",
        AsyncMock(
            return_value=analysis_result
        ),
    )

    response = MagicMock()
    response.status_code = 200
    response.text = "not-json"

    response.json.side_effect = ValueError(
        "invalid json"
    )

    response.raise_for_status.return_value = None

    _mock_http_client(
        monkeypatch,
        response,
    )

    await main.run_analysis_and_callback(
        links=["https://example.com"],
        message="테스트 문자",
        callback_url="https://callback.example.com",
        user_id="test-user",
        job_id=job_id,
    )

    job = main.ANALYSIS_JOBS[job_id]

    # 분석 자체는 정상 완료
    assert job["status"] == "completed"
    assert job["result"] == analysis_result

    # callback 응답만 해석하지 못함
    assert job["callback_status"] == "failed"
    assert (
        job["callback_error"]
        == "invalid_callback_response"
    )

    assert "test-user" not in main.RUNNING_USERS


@pytest.mark.asyncio
async def test_callback_request_exception(
    monkeypatch,
):
    """callback HTTP 요청 자체가 실패하면 예외 원인을 저장한다."""
    job_id = "job-callback-exception"

    _create_job(job_id)

    analysis_result = main.kakao_response(
        "분석 완료"
    )

    monkeypatch.setattr(
        main,
        "run_analysis",
        AsyncMock(
            return_value=analysis_result
        ),
    )

    client = MagicMock()

    client.post = AsyncMock(
        side_effect=RuntimeError(
            "callback connection failed"
        )
    )

    monkeypatch.setattr(
        main,
        "get_http_client",
        lambda: client,
    )

    await main.run_analysis_and_callback(
        links=["https://example.com"],
        message="테스트 문자",
        callback_url="https://callback.example.com",
        user_id="test-user",
        job_id=job_id,
    )

    job = main.ANALYSIS_JOBS[job_id]

    # callback 전송이 실패해도 분석 결과는 성공 상태를 유지한다.
    assert job["status"] == "completed"
    assert job["result"] == analysis_result
    assert job["error"] is None

    assert job["callback_status"] == "failed"

    assert job["callback_error"] == (
        "RuntimeError: callback connection failed"
    )

    assert "test-user" not in main.RUNNING_USERS