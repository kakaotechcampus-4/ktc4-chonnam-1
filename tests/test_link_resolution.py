"""단축 주소 해석: 격리 서버 2단계 경로에서 첫 실제 도메인을 찾아 공식 여부를 판정한다."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from scanner.collect_response import parse_collect_response
from scanner.fetch import collect_url
from scanner.models import CollectResult
from services.link_resolution import first_real_host, is_shortener, resolve_real_host

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "isolation"))
from collector.app import create_app  # noqa: E402
from collector.policy import is_allowed_url  # noqa: E402


def collected(link, chain=(), final=None, failures=()):
    return CollectResult(
        input_url=link, final_url=final, redirect_chain=tuple(chain), status_code=None,
        content_type=None, html="", title=None, elapsed_ms=1, failures=tuple(failures),
    )


class TestFirstRealHost:
    def test_skips_shortener(self):
        assert first_real_host("https://bit.ly/a", ["https://www.cjlogistics.com/x"]) == "www.cjlogistics.com"

    def test_attacker_behind_shortener(self):
        chain = ["https://cj-delivery.xyz/a", "https://www.cjlogistics.com/"]
        assert first_real_host("https://bit.ly/a", chain) == "cj-delivery.xyz"

    def test_shortener_chain(self):
        assert first_real_host("https://bit.ly/a", ["https://me2.do/b", "https://evil.example/"]) == "evil.example"

    def test_only_shorteners(self):
        assert first_real_host("https://bit.ly/a", ["https://me2.do/b"]) is None

    def test_no_chain(self):
        assert first_real_host("https://bit.ly/a", []) is None

    def test_not_shortener_returns_input(self):
        assert first_real_host("https://www.epost.go.kr/x", []) == "www.epost.go.kr"

    def test_is_shortener(self):
        assert is_shortener("BIT.LY.") and not is_shortener("bit.ly.evil.example") and not is_shortener(None)


class TestResolveRealHost:
    async def test_uses_redirect_chain(self):
        async def collect(link):
            return collected(link, ["https://cj-delivery.xyz/a"], "https://cj-delivery.xyz/a", ("blocked_address",))

        assert await resolve_real_host("https://bit.ly/a", collect) == "cj-delivery.xyz"

    async def test_failure_is_none(self):
        async def collect(link):
            raise RuntimeError("isolation down")

        assert await resolve_real_host("https://bit.ly/a", collect) is None

    async def test_shortener_blocked_by_isolation_is_none(self):
        # 격리 서버 허용 목록에 없는 단축 도메인: 요청을 보내지 않아 경로가 비어 있다
        async def collect(link):
            return collected(link, (), link, ("blocked_address",))

        assert await resolve_real_host("https://bit.ly/a", collect) is None


class TestJudgement:
    PARSED = {"final_url": "https://www.cjlogistics.com/ko/main", "domain": "www.cjlogistics.com", "score": 0}

    def test_shortener_to_official(self):
        r = main.build_ai_url_analysis(self.PARSED, "CJ대한통운", "https://bit.ly/a", judged_domain="www.cjlogistics.com")
        assert r.official.value == "official"

    def test_shortener_to_attacker_cloaking_to_official(self):
        r = main.build_ai_url_analysis(self.PARSED, "CJ대한통운", "https://bit.ly/a", judged_domain="cj-delivery.xyz")
        assert r.official.value == "brand_mismatch"
        assert r.domain == "cj-delivery.xyz"

    def test_unresolved_shortener(self):
        r = main.build_ai_url_analysis(self.PARSED, "CJ대한통운", "https://bit.ly/a", judged_domain=None)
        assert r.official.value == "unresolved"

    def test_without_shortener_uses_link(self):
        r = main.build_ai_url_analysis(self.PARSED, "CJ대한통운", "https://cj-delivery.xyz/a")
        assert r.official.value == "brand_mismatch"


class TestEndToEndWithCollector:
    """격리 서버 수집 API(실제 앱·fetch.py) → 메인 서버 파서 → 첫 실제 도메인."""

    async def run(self, routes):
        hosts = frozenset({"bit.ly", "me2.do"})
        sent: list[str] = []

        async def handler(request):
            sent.append(str(request.url))
            location = routes.get(str(request.url))
            if location:
                return httpx.Response(301, headers={"location": location})
            return httpx.Response(200, text="<p>안내 페이지</p>", headers={"content-type": "text/html"})

        async def public_resolve(host):
            return ["93.184.216.34"]

        async def collect_with_mock(url, *, timeout, url_allowed):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await collect_url(url, client=client, resolve=public_resolve, timeout=timeout, url_allowed=url_allowed)

        app = create_app(token="t" * 40, allowed_hosts=hosts, collect=collect_with_mock)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://c") as c:
            async def collect(link):
                r = await c.post("/collect", content=json.dumps({"url": link}),
                                 headers={"Authorization": "Bearer " + "t" * 40})
                return parse_collect_response(link, r.json())

            return await resolve_real_host(next(iter(routes)), collect), sent

    async def test_attacker_site_is_never_requested(self):
        real, sent = await self.run({
            "https://bit.ly/a": "https://me2.do/b",
            "https://me2.do/b": "https://cj-delivery.xyz/track",
        })
        assert real == "cj-delivery.xyz"
        assert sent == ["https://bit.ly/a", "https://me2.do/b"]  # 공격자 주소로는 요청하지 않았다

    async def test_official_destination(self):
        real, sent = await self.run({"https://bit.ly/a": "https://www.cjlogistics.com/ko/main"})
        assert real == "www.cjlogistics.com"
        assert sent == ["https://bit.ly/a"]


async def test_run_analysis_resolves_shortener(monkeypatch):
    link = "https://bit.ly/abc"
    seen = {}

    async def fake_resolve(url):
        seen["resolve"] = url
        return "cj-delivery.xyz"

    def fake_build(parsed_result, brand, input_url, **kwargs):
        seen["judged_domain"] = kwargs.get("judged_domain")
        return SimpleNamespace(domain="cj-delivery.xyz", official="brand_mismatch")

    class FakeMessage:
        brand = "CJ대한통운"

        def model_dump(self, mode="json"):
            return {"brand": "CJ대한통운"}

    class FakeFinal:
        def model_dump(self, mode="json"):
            return {}

    monkeypatch.setattr(main, "ENABLE_URL_COLLECTOR_SHADOW", False)
    monkeypatch.setattr(main, "resolve_real_host", fake_resolve)
    monkeypatch.setattr(main, "submit_url_scan", AsyncMock(return_value={"uuid": "s"}))
    monkeypatch.setattr(main, "wait_for_url_scan_result", AsyncMock(return_value={"x": 1}))
    monkeypatch.setattr(main, "parse_urlscan_result", lambda r: {"final_url": "https://www.cjlogistics.com/", "domain": "www.cjlogistics.com", "score": 0})
    monkeypatch.setattr(main, "analyze_message_part", AsyncMock(return_value=FakeMessage()))
    monkeypatch.setattr(main, "finalize_analysis", AsyncMock(return_value=FakeFinal()))
    monkeypatch.setattr(main, "build_ai_url_analysis", fake_build)
    monkeypatch.setattr(main, "render_result_card", lambda payload, **kw: {})
    monkeypatch.setattr(main, "merge_kakao_responses", lambda responses: responses)

    await asyncio.wait_for(main.run_analysis(links=[link], message="m"), timeout=2)
    assert seen == {"resolve": link, "judged_domain": "cj-delivery.xyz"}
