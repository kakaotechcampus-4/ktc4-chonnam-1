"""scanner.fetch.collect_url 테스트.

외부 네트워크에 의존하지 않도록 httpx.MockTransport 로 응답을 흉내 낸다.
오늘 작업 계획(담당 B)의 테스트 체크리스트를 그대로 따른다: 정상 페이지,
redirect, timeout, 잘못된 URL, localhost/private IP, HTML 이 아닌 응답,
너무 큰 응답.
"""

import asyncio

import httpx

from scanner.fetch import HTML_LIMIT_BYTES, collect_url
from scanner.models import CollectResult

PUBLIC_IP = "93.184.216.34"


async def resolve_stub(host: str) -> list[str]:
    return {"internal.test": ["10.0.0.5"]}.get(host, [PUBLIC_IP])


def run(url: str, routes: dict[str, httpx.Response], *, slow: frozenset[str] = frozenset(),
        timeout: float = 10.0) -> tuple[CollectResult, list[str]]:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url) in slow:
            await asyncio.sleep(1)
        if str(request.url) not in routes:
            raise httpx.ConnectError("unreachable", request=request)
        return routes[str(request.url)]

    async def go() -> CollectResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_url(url, client=client, resolve=resolve_stub, timeout=timeout)

    return asyncio.run(go()), seen


def redirect(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to})


def page(html: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, text=html)


class TestNormalPage:
    def test_collects_status_html_and_title(self):
        result, seen = run("https://example.test/", {
            "https://example.test/": page("<html><head><title> 배송 조회 </title></head>"
                                           "<body>hello</body></html>"),
        })

        assert result.status_code == 200
        assert result.final_url == "https://example.test/"
        assert result.content_type.startswith("text/html")
        assert "hello" in result.html
        assert result.title == "배송 조회"
        assert result.failures == ()
        assert result.elapsed_ms >= 0
        assert seen == ["https://example.test/"]


class TestRedirect:
    def test_follows_redirect_chain_and_records_it(self):
        result, _ = run("https://bit.ly/a", {
            "https://bit.ly/a": redirect("https://hop.example/b"),
            "https://hop.example/b": redirect("/c"),
            "https://hop.example/c": page("<p>landing</p>"),
        })

        assert result.final_url == "https://hop.example/c"
        assert result.redirect_chain == ("https://hop.example/b", "https://hop.example/c")
        assert result.status_code == 200
        assert result.failures == ()

    def test_redirect_limit_is_enforced(self):
        routes = {
            f"https://r.example/{i}": redirect(f"https://r.example/{i + 1}") for i in range(7)
        }

        result, _ = run("https://r.example/0", routes)

        assert result.failures == ("redirect_limit",)
        assert len(result.redirect_chain) == 6  # max_redirects(5) + 마지막 홉


class TestTimeout:
    def test_slow_response_is_reported_as_timeout_not_raised(self):
        result, _ = run(
            "https://slow.example/",
            {"https://slow.example/": page("<p>late</p>")},
            slow=frozenset({"https://slow.example/"}),
            timeout=0.2,
        )

        assert result.failures == ("timeout",)
        assert result.html == ""

    def test_timeout_during_redirect_keeps_chain_so_far(self):
        result, _ = run(
            "https://bit.ly/a",
            {
                "https://bit.ly/a": redirect("https://evil.example/pay"),
                "https://evil.example/pay": page("<p>late</p>"),
            },
            slow=frozenset({"https://evil.example/pay"}),
            timeout=0.2,
        )

        assert result.failures == ("timeout",)
        assert result.redirect_chain == ("https://evil.example/pay",)


class TestInvalidUrl:
    def test_non_http_scheme_is_rejected_without_a_request(self):
        result, seen = run("ftp://files.example/a", {})

        assert result.failures == ("invalid_scheme",)
        assert seen == []

    def test_javascript_scheme_is_rejected(self):
        result, seen = run("javascript:alert(1)", {})

        assert result.failures == ("invalid_scheme",)
        assert seen == []

    def test_unreachable_host_is_connection_failed(self):
        result, _ = run("https://down.example/", {})

        assert result.failures == ("connection_failed",)


class TestBlockedAddress:
    def test_localhost_literal_is_blocked_without_a_request(self):
        result, seen = run("http://127.0.0.1/admin", {})

        assert result.failures == ("blocked_address",)
        assert seen == []

    def test_link_local_metadata_ip_is_blocked(self):
        result, seen = run("http://169.254.169.254/latest/meta-data/", {})

        assert result.failures == ("blocked_address",)
        assert seen == []

    def test_hostname_resolving_to_private_ip_is_blocked(self):
        result, seen = run(
            "https://internal.test/admin", {"https://internal.test/admin": page("<p>x</p>")}
        )

        assert result.failures == ("blocked_address",)
        assert seen == []

    def test_redirect_target_resolving_to_private_ip_is_blocked(self):
        result, seen = run(
            "https://a.example/", {"https://a.example/": redirect("http://internal.test/admin")}
        )

        assert result.failures == ("blocked_address",)
        assert result.final_url == "http://internal.test/admin"
        assert seen == ["https://a.example/"]


class TestNonHtmlResponse:
    def test_binary_response_is_not_decoded(self):
        binary = httpx.Response(
            200, headers={"content-type": "application/vnd.android.package-archive"},
            content=b"PK\x03\x04",
        )

        result, _ = run("https://evil.example/app.apk", {"https://evil.example/app.apk": binary})

        assert result.status_code == 200
        assert result.content_type == "application/vnd.android.package-archive"
        assert result.html == ""
        assert result.title is None
        assert result.failures == ()


class TestTooLargeResponse:
    def test_html_over_limit_is_truncated_and_noted(self):
        result, _ = run(
            "https://big.example/", {"https://big.example/": page("a" * (HTML_LIMIT_BYTES + 10))}
        )

        assert result.failures == ("html_truncated",)
        assert len(result.html) == HTML_LIMIT_BYTES
