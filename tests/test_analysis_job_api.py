"""GET /api/analyses/{job_id} 조회 API 테스트.

쓰기 쪽(run_analysis_and_callback이 Job 상태를 어떻게 만드는지)은 건드리지
않는다 — 여기서는 ANALYSIS_JOBS에 이미 들어있는 각 상태를 조회했을 때
일관된 모양으로 돌아오는지만 확인한다. FE 카드·카카오 버튼(clientExtra)
연결은 이번 범위가 아니다.
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def clean_jobs():
    """테스트마다 ANALYSIS_JOBS를 비워서 테스트 간 상태가 섞이지 않게 한다."""
    main.ANALYSIS_JOBS.clear()
    yield
    main.ANALYSIS_JOBS.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _base_job(job_id: str, **overrides) -> dict:
    job = {
        "job_id": job_id,
        "user_id": "kakao-user-1",
        "status": "running",
        "created_at": "2026-10-01T00:00:00+00:00",
        "completed_at": None,
        "result": None,
        "error": None,
        "callback_status": "pending",
        "callback_error": None,
    }
    job.update(overrides)
    return job


class TestGetAnalysisJob:
    def test_nonexistent_job_returns_404(self, client):
        response = client.get("/api/analyses/does-not-exist")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_running_job(self, client):
        main.ANALYSIS_JOBS["job-running"] = _base_job("job-running")

        response = client.get("/api/analyses/job-running")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["job"]["status"] == "running"
        assert body["job"]["completed_at"] is None
        assert body["job"]["result"] is None
        assert body["job"]["error"] is None
        assert body["job"]["callback_status"] == "pending"

    def test_completed_job(self, client):
        main.ANALYSIS_JOBS["job-completed"] = _base_job(
            "job-completed",
            status="completed",
            completed_at="2026-10-01T00:00:05+00:00",
            result={"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "ok"}}]}},
            error=None,
            callback_status="success",
        )

        response = client.get("/api/analyses/job-completed")

        assert response.status_code == 200
        job = response.json()["job"]
        assert job["status"] == "completed"
        assert job["error"] is None
        assert job["result"] is not None
        assert job["callback_status"] == "success"

    def test_failed_job(self, client):
        main.ANALYSIS_JOBS["job-failed"] = _base_job(
            "job-failed",
            status="failed",
            completed_at="2026-10-01T00:00:05+00:00",
            result={"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석 중 문제가 발생했습니다."}}]}},
            error="RuntimeError: boom",
            callback_status="failed",
            callback_error="kakao_callback_status=FAIL",
        )

        response = client.get("/api/analyses/job-failed")

        assert response.status_code == 200
        job = response.json()["job"]
        assert job["status"] == "failed"
        assert job["error"] is not None
        # 실패해도 사용자에게 보여줄 결과(폴백 메시지)는 비어있지 않아야 한다.
        assert job["result"] is not None

        assert job["callback_status"] == "failed"
        assert job["callback_error"] == "kakao_callback_status=FAIL"

    def test_timeout_job(self, client):
        main.ANALYSIS_JOBS["job-timeout"] = _base_job(
            "job-timeout",
            status="timeout",
            completed_at="2026-10-01T00:00:45+00:00",
            result={"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "분석이 예상보다 오래 걸리고 있어요."}}]}},
            error="analysis_timeout",
            callback_status="pending",
        )

        response = client.get("/api/analyses/job-timeout")

        assert response.status_code == 200
        job = response.json()["job"]
        assert job["status"] == "timeout"
        assert job["error"] == "analysis_timeout"
        assert job["result"] is not None

    def test_callback_status_pending_before_send_attempted(self, client):
        """콜백 전송 시도 전(분석 중)에는 callback_status가 pending이어야 한다."""
        main.ANALYSIS_JOBS["job-pending-callback"] = _base_job("job-pending-callback")

        response = client.get(
            "/api/analyses/job-pending-callback"
        )
    
        job = response.json()["job"]
    
        assert job["callback_status"] == "pending"
        assert job["callback_error"] is None

    def test_multiple_jobs_are_looked_up_independently(self, client):
        main.ANALYSIS_JOBS["job-a"] = _base_job("job-a", status="running")
        main.ANALYSIS_JOBS["job-b"] = _base_job("job-b", status="completed")

        response_a = client.get("/api/analyses/job-a")
        response_b = client.get("/api/analyses/job-b")

        assert response_a.json()["job"]["status"] == "running"
        assert response_b.json()["job"]["status"] == "completed"

    def test_response_always_has_success_and_job_keys(self, client):
        main.ANALYSIS_JOBS["job-shape"] = _base_job("job-shape")

        response = client.get("/api/analyses/job-shape")

        assert set(response.json().keys()) == {"success", "job"}

    def test_job_payload_has_stable_field_set_across_statuses(self, client):
        """상태가 달라도 job 딕셔너리의 필드 구성 자체는 동일해야 FE가
        매번 다른 모양을 처리하지 않아도 된다."""
        expected_keys = {
            "job_id", "user_id", "status", "created_at",
            "completed_at", "result", "error",
            "callback_status", "callback_error",
        }

        for status in ("running", "completed", "failed", "timeout"):
            job_id = f"job-{status}"
            main.ANALYSIS_JOBS[job_id] = _base_job(job_id, status=status)

            response = client.get(f"/api/analyses/{job_id}")

            assert set(response.json()["job"].keys()) == expected_keys, (
                f"status={status}에서 필드 구성이 다름"
            )
