"""멘토 리뷰 반영: 공식 주소 오안내, 콜백 주소 검사."""

import json

import pytest
from fastapi import BackgroundTasks

import main
from backend.src.server.callback_guard import callback_host, is_allowed_callback_url
from services.official_domain_service import check_link_official, official_homepage
from services.result_card_renderer import _result_card_values

WL = {"CJ대한통운": {"cjlogistics.com"}}


class TestCheckLinkOfficial:
    def test_redirect_to_official_site_is_not_official(self):
        # 문자 속 링크는 공격자 주소, 최종 페이지만 진짜 사이트 (리다이렉트·클로킹)
        assert check_link_official("CJ대한통운", "cj-delivery.xyz", "www.cjlogistics.com", whitelist=WL) == "brand_mismatch"

    def test_official_link_leaving_official_site_is_unresolved(self):
        # 공식 주소의 오픈 리다이렉트 가능성. 공식이라고 안내하지 않는다
        assert check_link_official("CJ대한통운", "www.cjlogistics.com", "evil.example", whitelist=WL) == "unresolved"

    def test_both_official(self):
        assert check_link_official("CJ대한통운", "trace.cjlogistics.com", "www.cjlogistics.com", whitelist=WL) == "official"

    def test_final_unknown_keeps_input_result(self):
        assert check_link_official("CJ대한통운", "www.cjlogistics.com", None, whitelist=WL) == "official"

    def test_unknown_brand(self):
        assert check_link_official(None, "naver.com", "naver.com", whitelist=WL) == "not_registered"

    def test_unparsed_input(self):
        assert check_link_official("CJ대한통운", None, "www.cjlogistics.com", whitelist=WL) == "unresolved"


class TestOfficialHomepageButton:
    def test_button_comes_from_whitelist_not_from_link(self):
        assert official_homepage("CJ대한통운", "trace.cjlogistics.com", whitelist=WL) == "https://cjlogistics.com"

    def test_no_button_when_not_on_whitelist(self):
        assert official_homepage("CJ대한통운", "cj-delivery.xyz", whitelist=WL) is None

    def test_r2_values_never_use_link_domain(self):
        values = _result_card_values(
            {"url": {"official": "official", "domain": "cj-delivery.xyz"}, "message": {"brand": "CJ대한통운"}},
            "r2-official",
        )
        assert "official_url" not in values


class TestCallbackGuard:
    @pytest.mark.parametrize("url", [
        "https://bot-api.kakao.com/v1/bots/abc/callback/token",
        "https://kakao.com/cb",
        "https://BOT-API.kakao.com:443/cb",
    ])
    def test_allowed_by_default(self, url, monkeypatch):
        monkeypatch.delenv("KAKAO_CALLBACK_HOSTS", raising=False)
        assert is_allowed_callback_url(url)

    @pytest.mark.parametrize("url", [
        "http://bot-api.kakao.com/cb",
        "https://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/latest/meta-data/",
        "https://localhost/cb",
        "https://127.0.0.1:8000/kakao/skill",
        "https://kakao.com.evil.example/cb",
        "https://evilkakao.com/cb",
        "https://evil.example/kakao.com",
        "https://bot-api.kakao.com@evil.example/cb",
        "https://bot-api.kakao.com:8443/cb",
        "https://evil.example\\@bot-api.kakao.com/cb",
        "https://bot-api.kakao.com/\tcb",
        "",
        None,
        123,
    ])
    def test_blocked(self, url, monkeypatch):
        monkeypatch.delenv("KAKAO_CALLBACK_HOSTS", raising=False)
        assert not is_allowed_callback_url(url)

    def test_exact_hosts_when_configured(self, monkeypatch):
        monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
        assert is_allowed_callback_url("https://bot-api.kakao.com/cb")
        assert not is_allowed_callback_url("https://other.kakao.com/cb")

    def test_host_for_log_drops_token(self):
        assert callback_host("https://bot-api.kakao.com/v1/callback/SECRET") == "bot-api.kakao.com"


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def skill_body(callback_url):
    return {
        "intent": {"name": "문자 확인"},
        "userRequest": {
            "utterance": "[CJ대한통운] 배송 확인 https://cj-delivery.xyz/a",
            "user": {"id": "u-test"},
            "callbackUrl": callback_url,
        },
    }


class TestKakaoSkillCallback:
    async def test_internal_callback_is_rejected_before_analysis(self, monkeypatch):
        monkeypatch.delenv("KAKAO_CALLBACK_HOSTS", raising=False)
        tasks = BackgroundTasks()
        response = await main.kakao_skill(FakeRequest(skill_body("http://169.254.169.254/latest")), tasks)
        assert tasks.tasks == []
        assert "u-test" not in main.RUNNING_USERS
        assert json.dumps(response, ensure_ascii=False) == json.dumps(main.render_card("r4-unavailable"), ensure_ascii=False)

    async def test_kakao_callback_starts_analysis(self, monkeypatch):
        monkeypatch.delenv("KAKAO_CALLBACK_HOSTS", raising=False)
        tasks = BackgroundTasks()
        try:
            await main.kakao_skill(FakeRequest(skill_body("https://bot-api.kakao.com/cb/t")), tasks)
            assert len(tasks.tasks) == 1
        finally:
            main.RUNNING_USERS.discard("u-test")

    async def test_send_step_refuses_bad_url_too(self, monkeypatch):
        posted = []

        class Client:
            async def post(self, *args, **kwargs):
                posted.append(args)

        async def fake_run(*args, **kwargs):
            return {"version": "2.0"}

        monkeypatch.setattr(main, "get_http_client", lambda: Client())
        monkeypatch.setattr(main, "run_analysis", fake_run)
        job_id = main.create_analysis_job("u-x")
        await main.run_analysis_and_callback(["https://a"], "m", "http://127.0.0.1:8000/x", "u-x", job_id)
        assert posted == []
        assert main.ANALYSIS_JOBS[job_id]["callback_status"] == "failed"
