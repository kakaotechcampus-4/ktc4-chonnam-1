""""자세히 보기" 버튼 클릭 처리 테스트 (이슈 #69).

결과 카드에 꽂힌 job_id가 action.clientExtra로 돌아왔을 때, 분석을 다시
하지 않고 저장해 둔 final_payload로 캐러셀만 다시 만드는지 검증한다.
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()
    yield
    main.ANALYSIS_JOBS.clear()
    main.RUNNING_USERS.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _job(job_id: str, final_payload=None, status: str = "completed") -> dict:
    main.ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": "kakao-user-1",
        "status": status,
        "created_at": "2026-10-13T00:00:00+00:00",
        "completed_at": "2026-10-13T00:00:05+00:00",
        "result": None,
        "final_payload": final_payload,
        "error": None,
        "callback_status": "pending",
        "callback_error": None,
    }
    return main.ANALYSIS_JOBS[job_id]


_ANALYSIS_RESULT = {
    "url": {"final_url": "https://example.com", "domain": "example.com", "official": "not_registered"},
    "message": {
        "brand": None, "category": None, "answer": "no_risk_found",
        "details": {"doubts": [], "signals": [], "reason": {"text": "x", "failures": []}},
    },
    "env": {
        "brand": None, "category": None, "answer": "not_run",
        "details": {"doubts": [], "signals": [], "reason": {"text": "x", "failures": []}},
    },
    "result": False,
}


class TestHandleDetailCarousel:
    def test_unknown_job_id_returns_fallback_text_without_crashing(self):
        response = main.handle_detail_carousel("no-such-job")

        assert "simpleText" in response["template"]["outputs"][0]
        assert response["template"]["outputs"][0]["simpleText"]["text"]

    def test_job_without_final_payload_returns_fallback(self):
        _job("job-1", final_payload=None, status="running")

        response = main.handle_detail_carousel("job-1")

        assert "simpleText" in response["template"]["outputs"][0]

    def test_job_with_final_payload_returns_carousel(self):
        _job("job-1", final_payload=_ANALYSIS_RESULT)

        response = main.handle_detail_carousel("job-1")

        assert "carousel" in response["template"]["outputs"][0]
        items = response["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == 2  # 신호 없음 → C2 + C3


class TestKakaoSkillRoutesClientExtraToDetailCarousel:
    def test_client_extra_job_id_skips_link_extraction(self, client):
        """버튼 클릭 바디에는 보통 링크가 없는 발화가 들어오는데,
        clientExtra가 있으면 그 발화를 분석 대상으로 보지 않아야 한다."""
        _job("job-1", final_payload=_ANALYSIS_RESULT)

        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {
                    "utterance": "자세히 보기",
                    "user": {"id": "kakao-user-1"},
                },
                "action": {"clientExtra": {"job_id": "job-1"}},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "carousel" in body["template"]["outputs"][0]

    def test_missing_job_falls_back_without_touching_running_users(self, client):
        response = client.post(
            "/kakao/skill",
            json={
                "userRequest": {
                    "utterance": "자세히 보기",
                    "user": {"id": "kakao-user-1"},
                },
                "action": {"clientExtra": {"job_id": "does-not-exist"}},
            },
        )

        assert response.status_code == 200
        assert "kakao-user-1" not in main.RUNNING_USERS
