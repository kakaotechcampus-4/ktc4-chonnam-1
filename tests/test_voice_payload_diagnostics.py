"""카카오 음성 메시지 SkillPayload 진단 로그 테스트.

실제 다운로드·Mock 분석·카드 렌더링은 아직 없다 — `userRequest.params`
등에 음성 미디어 필드가 있는지 확인하는 임시 로그만 검증한다. URL·
토큰·사용자 ID 같은 민감한 값이 로그에 그대로 찍히지 않는지가 핵심이다.
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


SENSITIVE_URL = "https://media.kakao.test/voice/private-token-abc123.m4a"
SENSITIVE_USER_ID = "kakao-voice-user-secret-id"


def _voice_payload(**param_overrides) -> dict:
    params = {
        "media": {
            "type": "audio",
            "url": SENSITIVE_URL,
            "name": "recording.m4a",
        },
    }
    params.update(param_overrides)
    return {
        "userRequest": {
            "utterance": "",
            "user": {"id": SENSITIVE_USER_ID},
            "params": params,
        },
    }


class TestVoicePayloadDiagnostics:
    def test_hypothesized_media_payload_is_logged(self, client, capsys):
        client.post("/kakao/skill", json=_voice_payload())

        output = capsys.readouterr().out

        assert "[VOICE DIAG]" in output
        assert "media" in output
        assert "type='audio'" in output
        assert "has_url=True" in output

    def test_payload_without_media_logs_empty_field_types(self, client, capsys):
        client.post(
            "/kakao/skill",
            json={
                "userRequest": {
                    "utterance": "결과 확인",
                    "user": {"id": "plain-text-user"},
                },
            },
        )

        output = capsys.readouterr().out

        assert "[VOICE DIAG] userRequest.params field_types={}" in output

    def test_sensitive_values_are_never_logged(self, client, capsys):
        """[VOICE DIAG] 줄 자체에는 URL·사용자 ID가 없어야 한다.

        [KAKAO] 등 기존 로그는 이미 user_id를 찍고 있고 이번 범위가
        아니라, 여기서는 새로 추가한 [VOICE DIAG] 줄만 본다.
        """
        client.post("/kakao/skill", json=_voice_payload())

        output = capsys.readouterr().out
        diag_lines = "\n".join(
            line for line in output.splitlines() if line.startswith("[VOICE DIAG]")
        )

        assert diag_lines
        assert SENSITIVE_URL not in diag_lines
        assert SENSITIVE_USER_ID not in diag_lines

    def test_action_params_and_detail_params_field_types_are_logged(self, client, capsys):
        payload = _voice_payload()
        payload["action"] = {
            "params": {"some_param": "value"},
            "detailParams": {"some_param": {"origin": "value", "value": "value"}},
        }

        client.post("/kakao/skill", json=payload)

        output = capsys.readouterr().out

        assert "[VOICE DIAG] action.params field_types={'some_param': 'str'}" in output
        assert "[VOICE DIAG] action.detailParams field_types={'some_param': 'dict'}" in output

    def test_diagnostics_are_disabled_in_production(self, client, capsys, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("KAKAO_SKILL_SECRET", "s3cret")

        client.post(
            "/kakao/skill",
            json=_voice_payload(),
            headers={"X-Skill-Secret": "s3cret"},
        )

        output = capsys.readouterr().out

        assert "[VOICE DIAG]" not in output
