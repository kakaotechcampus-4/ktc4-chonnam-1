from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_skill_echo():
    payload = {"userRequest": {"utterance": "테스트"}}
    body = client.post("/skill", json=payload).json()
    assert body["version"] == "2.0"
    assert "테스트" in body["template"]["outputs"][0]["simpleText"]["text"]
