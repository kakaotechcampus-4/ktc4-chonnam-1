from fastapi.testclient import TestClient

from experiments.voice_upload_probe import app as probe


def _body(utterance: str, callback_url: str | None = None, params: dict | None = None) -> dict:
    user_request = {"utterance": utterance, "user": {"id": "u1"}, "block": {"name": "폴백 블록"}}
    if callback_url:
        user_request["callbackUrl"] = callback_url
    return {"userRequest": user_request, "action": {"params": params or {}}}


def test_find_urls_reports_path_including_json_string_params():
    body = _body(
        "https://talk.kakaocdn.net/a.m4a",
        params={"file": '{"url": "https://dn.kakaocdn.net/b.m4a"}'},
    )
    found = probe.find_urls(body)
    assert ("$.userRequest.utterance", "https://talk.kakaocdn.net/a.m4a") in found
    assert ("$.action.params.file<json>.url", "https://dn.kakaocdn.net/b.m4a") in found


def test_find_urls_ignores_callback_url():
    assert probe.find_urls(_body("hi", callback_url="https://bot-api.kakao.com/cb?token=x")) == []


def test_media_url_allows_only_https_kakao_hosts():
    assert probe.is_allowed_media_url("https://talk.kakaocdn.net/a.m4a")
    assert not probe.is_allowed_media_url("http://talk.kakaocdn.net/a.m4a")
    assert not probe.is_allowed_media_url("https://evil.example/a.m4a")
    assert not probe.is_allowed_media_url("https://169.254.169.254/latest")


def test_mask_body_hides_callback_token():
    masked = probe.mask_body(_body("hi", callback_url="https://bot-api.kakao.com/cb/secret-token"))
    assert "secret-token" not in str(masked)


def test_without_callback_echoes_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "LOG_DIR", tmp_path)
    response = TestClient(probe.app).post("/skill", json=_body("https://talk.kakaocdn.net/a.m4a"))
    text = response.json()["template"]["outputs"][0]["simpleText"]["text"]
    assert "콜백 없음" in text and "talk.kakaocdn.net" in text
    assert list(tmp_path.glob("*-payload.json"))


def test_with_callback_returns_use_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "LOG_DIR", tmp_path)
    calls = []

    async def fake_probe(*args):
        calls.append(args)

    monkeypatch.setattr(probe, "probe_and_callback", fake_probe)
    body = _body("https://talk.kakaocdn.net/a.m4a", "https://bot-api.kakao.com/cb/t")
    # 실제 카톡 사진 payload 처럼 같은 주소가 params.media.url 에도 온다.
    body["userRequest"]["params"] = {"media": {"type": "image", "url": "https://talk.kakaocdn.net/a.m4a"}}
    response = TestClient(probe.app).post("/skill", json=body)
    assert response.json()["useCallback"] is True
    assert calls[0][0] == ["https://talk.kakaocdn.net/a.m4a"]
