"""카카오 '결과 확인' 기능 테스트.

사용자의 가장 최근 분석 Job을 조회하고 Job 상태에 따라
적절한 카카오 응답을 반환하는지 검증한다.

실제 AI 분석, urlscan, callback 요청은 수행하지 않는다.
"""

import pytest
from fastapi.testclient import TestClient

import main
from ai.types import AnswerState, MessageDetails, MessagePart, Reason


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch):
    """각 테스트 사이에서 Job 상태가 섞이지 않도록 초기화한다.

    APP_ENV=test로 고정해 verify_skill_secret()의 스킬 시크릿 검사를
    생략한다 — 이 파일은 /kakao/skill을 직접 호출하는데, 시크릿 검사는
    카카오 관리자센터가 실제로 호출하는 환경(운영)만 지키면 된다.
    """
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


def _create_job(
    job_id: str,
    user_id: str,
    status: str = "running",
    created_at: str = "2026-10-02T00:00:00+00:00",
    completed_at: str | None = None,
    result=None,
):
    """결과 확인 테스트용 Job을 저장한다."""
    main.ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "created_at": created_at,
        "completed_at": completed_at,
        "result": result,
        "error": None,
        "callback_status": "pending",
        "callback_error": None,
    }

    return main.ANALYSIS_JOBS[job_id]


class TestFindLatestJobByUser:

    def test_returns_none_when_user_has_no_job(self):
        result = main.find_latest_job_by_user(
            "user-without-job"
        )

        assert result is None

    def test_returns_latest_job(self):
        _create_job(
            "old-job",
            "user-1",
            created_at="2026-10-02T00:00:00+00:00",
        )

        _create_job(
            "new-job",
            "user-1",
            created_at="2026-10-02T00:10:00+00:00",
        )

        result = main.find_latest_job_by_user(
            "user-1"
        )

        assert result is not None
        assert result["job_id"] == "new-job"

    def test_does_not_return_other_users_job(self):
        _create_job(
            "user-a-job",
            "user-a",
            created_at="2026-10-02T00:00:00+00:00",
        )

        _create_job(
            "user-b-job",
            "user-b",
            created_at="2026-10-02T00:10:00+00:00",
        )

        result = main.find_latest_job_by_user(
            "user-a"
        )

        assert result is not None
        assert result["job_id"] == "user-a-job"

    def test_none_user_id_returns_none(self):
        _create_job(
            "some-job",
            "user-1",
        )

        assert main.find_latest_job_by_user(None) is None


class TestHandleCheckResult:

    def test_no_job_returns_k4(self):
        response = main.handle_check_result(
            "user-without-job"
        )

        expected = main.render_card(
            "k4-no-result"
        )

        assert response == expected

    def test_running_job_returns_k1(self):
        _create_job(
            "running-job",
            "user-1",
            status="running",
        )

        response = main.handle_check_result(
            "user-1"
        )

        expected = main.render_card(
            "k1-still-running"
        )

        assert response == expected

    def test_completed_job_returns_time_and_saved_result(self):
        saved_result = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "저장된 분석 결과"
                        }
                    }
                ]
            },
        }

        _create_job(
            "completed-job",
            "user-1",
            status="completed",
            completed_at="2026-10-02T10:23:00+00:00",
            result=saved_result,
        )

        response = main.handle_check_result(
            "user-1"
        )

        outputs = response["template"]["outputs"]

        assert outputs[0] == {
            "simpleText": {
                "text": "19:23에 확인한 결과예요."
            }
        }

        assert outputs[1] == {
            "simpleText": {
                "text": "저장된 분석 결과"
            }
        }
        
    def test_completed_job_without_completed_at_returns_saved_result(
        self
    ):
        saved_result = main.kakao_response(
            "저장된 분석 결과"
        )

        _create_job(
            "completed-job",
            "user-1",
            status="completed",
            completed_at=None,
            result=saved_result,
        )

        response = main.handle_check_result(
            "user-1"
        )

        assert response == saved_result        

    def test_completed_job_without_result_returns_k4(self):
        _create_job(
            "completed-job",
            "user-1",
            status="completed",
            result=None,
        )

        response = main.handle_check_result(
            "user-1"
        )

        expected = main.render_card(
            "k4-no-result"
        )

        assert response == expected

    @pytest.mark.parametrize(
        "status",
        ["failed", "timeout"],
    )
    def test_failed_or_timeout_returns_r4(
        self,
        status,
    ):
        _create_job(
            f"{status}-job",
            "user-1",
            status=status,
        )

        response = main.handle_check_result(
            "user-1"
        )

        expected = main.render_card(
            "r4-unavailable"
        )

        assert response == expected


class TestCheckResultSkill:

    def test_check_result_is_handled_before_url_validation(
        self,
        client,
    ):
        """'결과 확인'은 URL 없는 일반 메시지로 처리되면 안 된다."""

        _create_job(
            "running-job",
            "kakao-user-1",
            status="running",
        )

        payload = {
            "intent": {
                "name": "결과 확인"
            },
            "userRequest": {
                "utterance": "결과 알려줘",
                "user": {
                    "id": "kakao-user-1"
                },
            }
        }

        response = client.post(
            "/kakao/skill",
            json=payload,
        )

        assert response.status_code == 200

        expected = main.render_card(
            "k1-still-running"
        )

        assert response.json() == expected
        
    def test_same_utterance_without_check_result_intent_does_not_query_job(
        self,
        client,
        monkeypatch,
    ):
        """발화 문자열이 같아도 결과 확인 intent가 아니면 Job 조회로 처리하지 않는다.

        "결과 확인"에는 링크가 없어 신규 시나리오(URL 없는 문자 분석)로
        빠진다 — 핵심은 어떤 카드가 나오냐가 아니라 Job을 조회하지
        않는다는 것이라, 문자 분석은 안전 쪽 결과로 고정해 둔다.
        """

        _create_job(
            "running-job",
            "kakao-user-1",
            status="running",
        )

        async def fake_analyze_message_part(text):
            return MessagePart(
                answer=AnswerState.NO_RISK_FOUND,
                details=MessageDetails(doubts=[], signals=[], reason=Reason(text="ok")),
            )

        monkeypatch.setattr(main, "analyze_message_part", fake_analyze_message_part)

        payload = {
            "intent": {
                "name": "다른 블록"
            },
            "userRequest": {
                "utterance": "결과 확인",
                "user": {
                    "id": "kakao-user-1"
                },
            }
        }

        response = client.post(
            "/kakao/skill",
            json=payload,
        )

        assert response.status_code == 200

        # 핵심 검증: "아직 확인하고 있어요"(Job 조회 결과)가 아니다.
        assert response.json() != main.render_card("k1-still-running")

        expected = main.render_card(
            "r6b"
        )

        assert response.json() == expected