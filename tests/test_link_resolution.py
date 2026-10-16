"""단축 주소 해석: 격리 서버 2단계 경로에서 첫 실제 도메인을 찾아 공식 여부를 판정한다."""

import json
import sys
from pathlib import Path

import httpx

from scanner.collect_response import parse_collect_response
from scanner.fetch import collect_url
from services.link_resolution import first_real_host, is_shortener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "isolation"))
from collector.app import create_app  # noqa: E402


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

            result = await collect(next(iter(routes)))
            return first_real_host(result.input_url, result.redirect_chain), sent

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
