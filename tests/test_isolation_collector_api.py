"""격리 서버 수집 API(infra/isolation/collector) 테스트.

실제 네트워크 없이 ASGI로 직접 호출한다. 응답은 메인 서버의 파서
(parse_collect_response)로 다시 읽어 두 쪽의 계약이 맞는지도 확인한다.
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "isolation"))

from collector.app import MAX_RESPONSE_BYTES, create_app, encode_result  # noqa: E402
from scanner.fetch import HTML_LIMIT_BYTES  # noqa: E402
from collector.policy import NetworkPolicy, is_allowed_url, parse_list  # noqa: E402
from scanner.collect_response import parse_collect_response  # noqa: E402
from scanner.fetch import collect_url  # noqa: E402
from scanner.models import BrowserResult, CollectResult  # noqa: E402

TOKEN = "t" * 40
HOSTS = frozenset({"fake.ktc-test.kr"})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OK_URL = "https://fake.ktc-test.kr/track?id=1"


def ok_result(url: str, **changes) -> CollectResult:
    base = CollectResult(
        input_url=url, final_url=url, redirect_chain=(), status_code=200,
        content_type="text/html", html="<title>배송 조회</title>", title="배송 조회",
        elapsed_ms=12, failures=(),
    )
    return CollectResult(**{**base.__dict__, **changes})


def client_for(collect=None, **kwargs) -> httpx.AsyncClient:
    calls: list[str] = []

    async def default_collect(url, **_):
        calls.append(url)
        return ok_result(url)

    app = create_app(token=TOKEN, network_policy=NetworkPolicy(allowed_hosts=HOSTS),
                     collect=collect or default_collect, **kwargs)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://collector")
    client.calls = calls  # type: ignore[attr-defined]
    return client


async def post(client, payload, headers=AUTH) -> httpx.Response:
    return await client.post("/collect", content=json.dumps(payload), headers=headers)


class TestAuth:
    async def test_missing_token_rejected(self):
        async with client_for() as c:
            assert (await post(c, {"url": OK_URL}, headers={})).status_code == 401
            assert c.calls == []

    async def test_wrong_token_rejected(self):
        async with client_for() as c:
            r = await post(c, {"url": OK_URL}, headers={"Authorization": "Bearer " + "x" * 40})
            assert r.status_code == 401

    def test_short_token_refuses_to_start(self):
        with pytest.raises(RuntimeError):
            create_app(token="short", network_policy=NetworkPolicy(allowed_hosts=HOSTS))

    def test_empty_allowlist_refuses_to_start(self):
        with pytest.raises(ValueError):
            NetworkPolicy(allowed_hosts=frozenset())


class TestRequestValidation:
    async def test_body_too_large(self):
        async with client_for() as c:
            r = await post(c, {"url": "https://fake.ktc-test.kr/" + "a" * 5000})
            assert r.status_code == 413

    async def test_invalid_json(self):
        async with client_for() as c:
            r = await c.post("/collect", content=b"{not json", headers=AUTH)
            assert r.status_code == 400

    async def test_extra_field_rejected(self):
        async with client_for() as c:
            assert (await post(c, {"url": OK_URL, "render": True})).status_code == 400

    async def test_non_string_url_rejected(self):
        async with client_for() as c:
            assert (await post(c, {"url": 123})).status_code == 400


class TestCollect:
    async def test_allowed_url_matches_main_server_contract(self):
        async with client_for() as c:
            r = await post(c, {"url": OK_URL})
        assert r.status_code == 200
        parsed = parse_collect_response(OK_URL, r.json())
        assert parsed.failures == ()
        assert parsed.title == "배송 조회"
        assert parsed.input_url == OK_URL

    async def test_blocked_host_is_not_collected(self):
        async with client_for() as c:
            r = await post(c, {"url": "https://example.com/"})
            assert c.calls == []
        assert parse_collect_response("https://example.com/", r.json()).failures == ("blocked_address",)

    async def test_busy_returns_503_immediately(self):
        release = asyncio.Event()

        async def slow_collect(url, **_):
            await release.wait()
            return ok_result(url)

        async with client_for(collect=slow_collect, max_concurrency=1) as c:
            first = asyncio.create_task(post(c, {"url": OK_URL}))
            await asyncio.sleep(0.05)
            second = await post(c, {"url": OK_URL})
            release.set()
            assert second.status_code == 503
            assert (await first).status_code == 200

    async def test_timeout_then_next_request_works(self):
        state = {"n": 0}

        async def hang_once(url, **_):
            state["n"] += 1
            if state["n"] == 1:
                await asyncio.sleep(60)
            return ok_result(url)

        async with client_for(collect=hang_once, fetch_timeout=0.05, fetch_retry_timeout=0.05) as c:
            first = await post(c, {"url": OK_URL})
            second = await post(c, {"url": OK_URL})
        assert first.json()["failures"] == ["timeout"]
        assert second.json()["failures"] == []

    async def test_crash_then_next_request_works(self):
        state = {"n": 0}

        async def crash_once(url, **_):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("boom")
            return ok_result(url)

        async with client_for(collect=crash_once) as c:
            first = await post(c, {"url": OK_URL})
            second = await post(c, {"url": OK_URL})
        assert first.json()["failures"] == ["collector_error"]
        assert second.status_code == 200 and second.json()["failures"] == []

    async def test_response_stays_under_main_server_limit(self):
        # 제어 문자는 JSON에서 6바이트(\u0001)로 커진다.
        async def escaping_collect(url, **_):
            return ok_result(url, html="\x01" * HTML_LIMIT_BYTES)

        async with client_for(collect=escaping_collect) as c:
            r = await post(c, {"url": OK_URL})
        assert len(r.content) <= MAX_RESPONSE_BYTES
        assert "html_truncated" in r.json()["failures"]


def browser_result(**changes) -> BrowserResult:
    base = BrowserResult(
        trigger="script_redirect", final_url=OK_URL + "&step=2", navigation_chain=(OK_URL,),
        html="<title>렌더링</title><form></form>", title="렌더링", downloads=(),
        blocked_requests=2, elapsed_ms=900, failures=(),
    )
    return BrowserResult(**{**base.__dict__, **changes})


class FakeRenderer:
    def __init__(self, result=None, hang=False):
        self.result = result or browser_result()
        self.hang = hang
        self.calls: list[tuple[str, str]] = []
        self.broken = False

    async def render(self, url, *, trigger, timeout):
        self.calls.append((url, trigger))
        if self.hang:
            await asyncio.sleep(60)
        return self.result

    async def start(self):
        pass

    async def close(self):
        pass

    def mark_broken(self):
        self.broken = True


class TestBrowserStage:
    async def test_off_by_default(self):
        async with client_for() as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["browser"] is None
        assert parse_collect_response(OK_URL, r.json()).browser is None

    async def test_auto_runs_only_when_trigger_fires(self):
        renderer = FakeRenderer()
        triggers = iter(["script_redirect", None])
        async with client_for(browser_mode="auto", renderer=renderer, trigger=lambda _: next(triggers)) as c:
            first = await post(c, {"url": OK_URL})
            second = await post(c, {"url": OK_URL})
        assert renderer.calls == [(OK_URL, "script_redirect")]
        parsed = parse_collect_response(OK_URL, first.json())
        assert parsed.browser == browser_result()
        assert parse_collect_response(OK_URL, second.json()).browser is None

    async def test_always_mode(self):
        renderer = FakeRenderer()
        async with client_for(browser_mode="always", renderer=renderer, trigger=lambda _: None) as c:
            await post(c, {"url": OK_URL})
        assert renderer.calls == [(OK_URL, "always")]

    async def test_blocked_host_never_reaches_browser(self):
        renderer = FakeRenderer()
        async with client_for(browser_mode="always", renderer=renderer, trigger=lambda _: "x") as c:
            await post(c, {"url": "https://example.com/"})
        assert renderer.calls == []

    async def test_hung_browser_is_cut_and_marked_broken(self, monkeypatch):
        import collector.app as app_module

        # 마감 여유는 앱을 만들 때 정해지므로 만들기 전에 줄인다.
        monkeypatch.setattr(app_module, "BACKSTOP_EXTRA_SECONDS", 0.05)
        renderer = FakeRenderer(hang=True)
        async with client_for(
            browser_mode="always", renderer=renderer, trigger=lambda _: "x",
            fetch_timeout=0.01, fetch_retry_timeout=0.01, browser_timeout=0.01,
        ) as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["failures"] == ["timeout"]
        assert renderer.broken

    async def test_both_htmls_fit_under_limit(self):
        async def big_collect(url, **_):
            return ok_result(url, html="\x01" * HTML_LIMIT_BYTES)

        renderer = FakeRenderer(browser_result(html='"' * HTML_LIMIT_BYTES))
        async with client_for(collect=big_collect, browser_mode="always", renderer=renderer, trigger=lambda _: "x") as c:
            r = await post(c, {"url": OK_URL})
        assert len(r.content) <= MAX_RESPONSE_BYTES
        parsed = parse_collect_response(OK_URL, r.json())
        assert "html_truncated" in parsed.failures
        assert parsed.browser is not None and parsed.browser.html

    def test_browser_mode_needs_renderer(self):
        with pytest.raises(RuntimeError):
            create_app(token=TOKEN, network_policy=NetworkPolicy(allowed_hosts=HOSTS), browser_mode="auto")


class TestParseBrowserField:
    def test_malformed_browser_is_failure_not_missing(self):
        data = {"input_url": OK_URL, "browser": "oops"}
        parsed = parse_collect_response(OK_URL, data)
        assert parsed.browser is not None
        assert parsed.browser.failures == ("invalid_response",)

    def test_lists_are_capped(self):
        data = {"input_url": OK_URL, "browser": {"navigation_chain": ["a"] * 50, "downloads": ["d"] * 50}}
        parsed = parse_collect_response(OK_URL, data)
        assert len(parsed.browser.navigation_chain) == 10
        assert len(parsed.browser.downloads) == 5
        assert "invalid_response" in parsed.browser.failures  # 이동 요청 기록이 없으면 대조할 수 없다

    def test_navigation_requests_are_read_and_capped(self):
        data = {"input_url": OK_URL, "browser": {"navigation_requests": ["a"] * 50}}
        parsed = parse_collect_response(OK_URL, data).browser
        assert len(parsed.navigation_requests) == 20
        assert "browser_navigation_limit" in parsed.failures

        data = {"input_url": OK_URL, "browser": {"navigation_requests": [OK_URL]}}
        parsed = parse_collect_response(OK_URL, data).browser
        assert parsed.navigation_requests == (OK_URL,)
        assert parsed.failures == ()

        data = {"input_url": OK_URL, "browser": {"navigation_requests": "bad"}}
        assert parse_collect_response(OK_URL, data).browser.navigation_requests == ()


class TestPolicy:
    @pytest.mark.parametrize("url", [
        "https://fake.ktc-test.kr/",
        "https://FAKE.ktc-test.kr/a?b=c",
        "https://fake.ktc-test.kr./",
        "https://fake.ktc-test.kr:443/",
    ])
    def test_allowed(self, url):
        assert is_allowed_url(url, HOSTS)

    @pytest.mark.parametrize("url", [
        "http://fake.ktc-test.kr/",
        "https://fake.ktc-test.kr:8443/",
        "https://fake.ktc-test.kr@evil.example/",
        "https://user@fake.ktc-test.kr/",
        "https://evil.example\\@fake.ktc-test.kr/",
        "https://fake.ktc-test.kr/\tx",
        "https://sub.fake.ktc-test.kr/",
        "https://2130706433/",
        "https://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "",
    ])
    def test_blocked(self, url):
        assert not is_allowed_url(url, HOSTS)

    def test_parse_list(self):
        assert parse_list(" Fake.ktc-test.kr. , ,b.test") == frozenset({"fake.ktc-test.kr", "b.test"})


class TestFetchAllowlistHook:
    async def test_redirect_outside_allowlist_is_not_requested(self):
        seen: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})

        async def resolve(host):
            return ["93.184.216.34"]

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await collect_url(
                OK_URL, client=client, resolve=resolve,
                url_allowed=lambda u: is_allowed_url(u, HOSTS),
            )
        assert result.failures == ("blocked_address",)
        assert seen == [OK_URL]
        assert result.redirect_chain == ("https://evil.example/steal",)


class TestMainClientCertPin:
    def test_no_pin_uses_system_ca(self, monkeypatch):
        from scanner.isolation_client import _tls_verify

        monkeypatch.delenv("ISOLATION_API_CA_CERT", raising=False)
        assert _tls_verify() is True

    def test_bad_pin_is_an_error_not_a_bypass(self, monkeypatch):
        from scanner.isolation_client import IsolationClientError, _tls_verify

        monkeypatch.setenv("ISOLATION_API_CA_CERT", "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----")
        with pytest.raises(IsolationClientError):
            _tls_verify()


class TestFetchRetry:
    """1차 수집이 시간 초과면 더 넉넉한 시간으로 한 번만 다시 받는다."""

    @staticmethod
    def collect_with(*results):
        timeouts: list[float] = []
        remaining = list(results)

        async def collect(url, *, timeout, **_):
            timeouts.append(timeout)
            return remaining.pop(0)(url)

        return collect, timeouts

    @staticmethod
    def timed_out(url):
        return ok_result(url, status_code=None, html="", failures=("timeout",))

    async def test_first_success_does_not_retry(self):
        collect, timeouts = self.collect_with(ok_result)
        async with client_for(collect=collect, fetch_timeout=2.0, fetch_retry_timeout=5.0) as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["failures"] == []
        assert timeouts == [2.0]

    async def test_timeout_retries_once_with_longer_limit(self, caplog):
        collect, timeouts = self.collect_with(self.timed_out, ok_result)
        with caplog.at_level("INFO", logger="collector"):
            async with client_for(collect=collect, fetch_timeout=2.0, fetch_retry_timeout=5.0) as c:
                r = await post(c, {"url": OK_URL})
        assert r.json()["failures"] == []
        assert r.json()["status_code"] == 200
        assert timeouts == [2.0, 5.0]
        assert "event=fetch_retry host=fake.ktc-test.kr" in caplog.text

    async def test_second_timeout_stays_a_failure(self):
        collect, timeouts = self.collect_with(self.timed_out, self.timed_out)
        async with client_for(collect=collect, fetch_timeout=2.0, fetch_retry_timeout=5.0) as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["failures"] == ["timeout"]
        assert timeouts == [2.0, 5.0]

    @pytest.mark.parametrize("failures", [("connection_failed",), ("blocked_address",), ("tls_cert_verify_failed",)])
    async def test_other_failures_do_not_retry(self, failures):
        collect, timeouts = self.collect_with(
            lambda url: ok_result(url, status_code=None, html="", failures=failures),
        )
        async with client_for(collect=collect, fetch_timeout=2.0, fetch_retry_timeout=5.0) as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["failures"] == list(failures)
        assert timeouts == [2.0]

    async def test_retry_success_still_reaches_browser(self):
        collect, _ = self.collect_with(self.timed_out, ok_result)
        renderer = FakeRenderer(browser_result())
        async with client_for(
            collect=collect, fetch_timeout=2.0, fetch_retry_timeout=5.0,
            browser_mode="auto", renderer=renderer, trigger=lambda _: "script_redirect",
        ) as c:
            r = await post(c, {"url": OK_URL})
        assert r.json()["browser"] is not None

    def test_default_budget_fits_main_server_timeout(self):
        from collector.app import (
            BACKSTOP_EXTRA_SECONDS, DEFAULT_BROWSER_TIMEOUT_SECONDS,
            DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS, DEFAULT_FETCH_TIMEOUT_SECONDS,
        )
        from scanner.isolation_client import ISOLATION_TIMEOUT_SECONDS

        assert DEFAULT_FETCH_TIMEOUT_SECONDS == 2.0
        assert DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS == 5.0
        assert (DEFAULT_FETCH_TIMEOUT_SECONDS + DEFAULT_FETCH_RETRY_TIMEOUT_SECONDS
                + DEFAULT_BROWSER_TIMEOUT_SECONDS + BACKSTOP_EXTRA_SECONDS) < ISOLATION_TIMEOUT_SECONDS


class TestStageTwoEgress:
    async def test_allowlist_mode_also_goes_through_proxy_without_tls_bypass(self):
        captured = {}

        async def collect(url, **kwargs):
            captured.update(kwargs)
            return ok_result(url)

        async with client_for(collect=collect) as c:
            assert (await post(c, {"url": OK_URL})).status_code == 200
        assert captured["allow_insecure_retry"] is False
        assert captured["client"]._mounts  # HTTPX installs the explicitly configured proxy transport.

    def test_long_redirect_urls_cannot_push_response_over_limit(self):
        long_url = "https://fake.ktc-test.kr/" + "a" * 200_000
        body = encode_result(ok_result(OK_URL, final_url=long_url, redirect_chain=(long_url,) * 6, html=""))
        assert len(body) <= MAX_RESPONSE_BYTES
        parsed = json.loads(body)
        assert all(len(hop) <= 2048 for hop in parsed["redirect_chain"])


async def test_non_ascii_content_length_is_rejected_not_crashed():
    async with client_for() as c:
        r = await c.post("/collect", content=json.dumps({"url": OK_URL}),
                         headers={**AUTH, "Content-Length": "²".encode("latin-1")})
        assert r.status_code == 413
