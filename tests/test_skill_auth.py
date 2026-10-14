"""verify_skill_secret() 테스트 (이슈 #66 BE 할 일 2).

/kakao/skill, /api/analyses/{job_id}, /test/kakao/r1-lookalike 가
X-Skill-Secret 헤더를 요구하는지 검증한다. local/test 환경은 예외라
기존 테스트가 헤더 없이 통과해야 한다(tests/test_check_result.py,
tests/test_analysis_job_api.py의 clean_jobs fixture가 이걸 보장한다).
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


SKILL_PAYLOAD = {
    "userRequest": {
        "utterance": "결과 확인",
        "user": {"id": "kakao-user-1"},
    }
}


class TestLocalAndTestEnvSkipTheCheck:
    def test_local_env_allows_request_without_header(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.delenv("KAKAO_SKILL_SECRET", raising=False)

        response = client.post("/kakao/skill", json=SKILL_PAYLOAD)

        assert response.status_code == 200

    def test_test_env_allows_request_without_header(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("KAKAO_SKILL_SECRET", raising=False)

        response = client.post("/kakao/skill", json=SKILL_PAYLOAD)

        assert response.status_code == 200


class TestProductionRequiresTheSecret:
    def test_missing_secret_config_rejects_every_request(self, client, monkeypatch):
        """운영 배포에 KAKAO_SKILL_SECRET 설정을 깜빡한 경우 — 빈 값끼리
        비교해 우연히 통과하면 안 된다."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("KAKAO_SKILL_SECRET", raising=False)

        response = client.post(
            "/kakao/skill",
            json=SKILL_PAYLOAD,
            headers={"X-Skill-Secret": ""},
        )

        assert response.status_code == 401

    def test_missing_header_is_rejected(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        response = client.post("/kakao/skill", json=SKILL_PAYLOAD)

        assert response.status_code == 401

    def test_wrong_secret_is_rejected(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        response = client.post(
            "/kakao/skill",
            json=SKILL_PAYLOAD,
            headers={"X-Skill-Secret": "wrong"},
        )

        assert response.status_code == 401

    def test_correct_secret_is_accepted(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        response = client.post(
            "/kakao/skill",
            json=SKILL_PAYLOAD,
            headers={"X-Skill-Secret": "s3cret"},
        )

        assert response.status_code == 200

    def test_staging_also_requires_the_secret(self, client, monkeypatch):
        """ENABLE_URL_COLLECTOR_SHADOW와 달리, staging도 운영과 같은
        카카오 관리자센터가 호출할 수 있으므로 예외를 두지 않는다."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        response = client.post("/kakao/skill", json=SKILL_PAYLOAD)

        assert response.status_code == 401


class TestSecretRotation:
    def test_previous_secret_is_accepted_during_rotation(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "new-secret")
        monkeypatch.setenv("KAKAO_SKILL_SECRET_PREVIOUS", "old-secret")

        response = client.post(
            "/kakao/skill",
            json=SKILL_PAYLOAD,
            headers={"X-Skill-Secret": "old-secret"},
        )

        assert response.status_code == 200

    def test_unset_previous_secret_does_not_match_empty_header(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "new-secret")
        monkeypatch.delenv("KAKAO_SKILL_SECRET_PREVIOUS", raising=False)

        response = client.post(
            "/kakao/skill",
            json=SKILL_PAYLOAD,
            headers={"X-Skill-Secret": ""},
        )

        assert response.status_code == 401


class TestOtherProtectedRoutes:
    def test_analysis_job_lookup_requires_secret_in_production(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")
        main.ANALYSIS_JOBS["job-1"] = {
            "job_id": "job-1",
            "user_id": "kakao-user-1",
            "status": "running",
            "created_at": "2026-10-01T00:00:00+00:00",
            "completed_at": None,
            "result": None,
            "error": None,
            "callback_status": "pending",
            "callback_error": None,
        }

        try:
            response = client.get("/api/analyses/job-1")
            assert response.status_code == 401

            response = client.get(
                "/api/analyses/job-1",
                headers={"X-Skill-Secret": "s3cret"},
            )
            assert response.status_code == 200
        finally:
            main.ANALYSIS_JOBS.clear()

    def test_r1_lookalike_test_route_requires_secret_in_production(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        response = client.post("/test/kakao/r1-lookalike")

        assert response.status_code == 401
