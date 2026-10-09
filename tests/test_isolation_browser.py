"""격리 서버 3단계 브라우저(infra/isolation/collector/browser.py) 테스트.

실제 Chromium 으로 로컬 가짜 페이지를 연다. playwright 나 브라우저가 없으면 건너뛴다.
"내부 서버"는 TCP 연결 수 자체를 센다. 요청이 한 번이라도 닿으면 실패다.

로컬은 root 로 돌 수 있어 크롬 샌드박스를 끄고 테스트한다. 운영 진입점
(collector.app.create_app_from_env)은 샌드박스를 끄는 길이 없고, EC2 의 verify.py 가
렌더러 프로세스에 --no-sandbox 가 없는지 확인한다.
"""

import os
import shutil
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "isolation"))

from collector.browser import BrowserRenderer, browser_trigger  # noqa: E402
from collector.egress_proxy import EgressProxy, parse_connect  # noqa: E402
from scanner.models import CollectResult  # noqa: E402


class CountingServer(ThreadingHTTPServer):
    """요청 줄을 읽기 전, TCP 연결 단계에서 센다."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connections = 0

    def process_request(self, request, client_address):
        self.connections += 1
        super().process_request(request, client_address)


class Internal(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"internal secret")

    def log_message(self, *args):
        pass


def make_public_handler(internal_port: int):
    inside = f"127.0.0.1:{internal_port}"
    pages = {
        "/form": '<title>배송 조회</title><form><input type="tel" name="p"></form>',
        "/js-redirect": '<title>이동</title><script>location.href="/form"</script>',
        "/js-out": f'<title>밖으로</title><script>location.href="http://{inside}/js-out"</script>',
        "/sub": (
            "<title>하위 자원</title>"
            f'<img src="http://{inside}/img">'
            f'<script src="https://{inside}/script.js"></script>'
            f'<link rel="stylesheet" href="http://{inside}/style.css">'
            f'<iframe src="http://{inside}/frame"></iframe>'
            "<script>"
            f'fetch("http://{inside}/fetch").catch(() => {{}});'
            f'try {{ new WebSocket("ws://{inside}/ws"); }} catch (e) {{}}'
            f'window.open("http://{inside}/popup");'
            "</script>"
        ),
        "/download": '<title>앱 설치</title><a id="d" href="/app.apk">설치</a>'
                     '<script>document.getElementById("d").click()</script>',
        "/busy": "<title>멈춤</title><script>while (true) {}</script>",
    }

    class Public(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/chain":
                return self._redirect("/chain2")
            if self.path == "/chain2":
                return self._redirect(f"http://{inside}/chain")
            if self.path == "/app.apk":
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="app.apk"')
                self.end_headers()
                self.wfile.write(b"PK\x03\x04 not really an apk")
                return
            body = pages.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def _redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *args):
            pass

    return Public


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("openssl 이 없어 로컬 HTTPS 가짜 페이지를 만들 수 없음")
    tmp = tmp_path_factory.mktemp("tls")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-days", "1", "-subj", "/CN=fake", "-addext", "subjectAltName=IP:127.0.0.1",
         "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.pem")],
        check=True, capture_output=True,
    )
    internal = CountingServer(("127.0.0.1", 0), Internal)
    public = CountingServer(("127.0.0.1", 0), make_public_handler(internal.server_address[1]))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp / "c.pem", tmp / "k.pem")
    public.socket = tls.wrap_socket(public.socket, server_side=True)
    for server in (internal, public):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield public.server_address[1], internal
    for server in (internal, public):
        server.shutdown()


@pytest.fixture
async def renderer(sites):
    port, _ = sites
    base = f"https://127.0.0.1:{port}/"
    r = BrowserRenderer(
        lambda url: url.startswith(base),
        EgressProxy(lambda host, p: host == "127.0.0.1" and p == port),
        sandbox=os.getuid() != 0,
        ignore_https_errors=True,  # 로컬 자체 서명 인증서용. 운영 진입점은 켜지 않는다
    )
    try:
        await r.start()
        if r._browser is None:
            pytest.skip("Chromium 을 띄울 수 없음")
        yield r
    finally:
        await r.close()


def url(sites, path):
    return f"https://127.0.0.1:{sites[0]}{path}"


async def test_renders_form_page(renderer, sites):
    result = await renderer.render(url(sites, "/form"), trigger="always", timeout=6)
    assert result.failures == ()
    assert result.title == "배송 조회"
    assert 'type="tel"' in result.html
    assert result.final_url == url(sites, "/form")


async def test_follows_js_redirect_inside_allowlist(renderer, sites):
    result = await renderer.render(url(sites, "/js-redirect"), trigger="script_redirect", timeout=6)
    assert result.final_url == url(sites, "/form")
    assert result.navigation_chain[-1] == url(sites, "/form")


async def test_js_redirect_outside_is_blocked(renderer, sites):
    before = sites[1].connections
    result = await renderer.render(url(sites, "/js-out"), trigger="script_redirect", timeout=6)
    assert sites[1].connections == before
    assert result.blocked_requests >= 1
    assert "browser_blocked_navigation" in result.failures
    # 이동을 막아도 이동을 시도한 페이지 화면은 남는다 (판정 근거로 쓸 수 있게)
    assert result.final_url == url(sites, "/js-out")
    assert result.title == "밖으로"
    assert "browser_timeout" not in result.failures  # 막힌 이동에서 마감까지 붙잡히지 않는다
    assert result.elapsed_ms < 3000


async def test_two_hop_server_redirect_is_blocked_by_proxy(renderer, sites):
    # route 는 /chain 만 보고 /chain2 이후는 보지 못한다. 프록시가 막아야 한다.
    before = sites[1].connections
    result = await renderer.render(url(sites, "/chain"), trigger="always", timeout=6)
    assert sites[1].connections == before
    assert "browser_blocked_navigation" in result.failures


async def test_subresources_never_reach_internal(renderer, sites):
    before = sites[1].connections
    result = await renderer.render(url(sites, "/sub"), trigger="always", timeout=6)
    assert sites[1].connections == before
    assert result.blocked_requests >= 5  # script, css, iframe, fetch, websocket, popup (+img)
    assert "browser_blocked_navigation" not in result.failures  # 팝업 이동은 메인 페이지 이동이 아니다
    assert result.title == "하위 자원"


async def test_download_is_recorded_not_saved(renderer, sites):
    result = await renderer.render(url(sites, "/download"), trigger="always", timeout=6)
    assert result.downloads == (url(sites, "/app.apk"),)
    assert result.failures == ()  # 다운로드가 시작되면 기다리지 않고 끝낸다
    assert result.elapsed_ms < 3000


async def test_busy_page_times_out_and_next_page_works(renderer, sites):
    busy = await renderer.render(url(sites, "/busy"), trigger="always", timeout=2)
    assert "browser_timeout" in busy.failures
    assert busy.elapsed_ms < 5000
    after = await renderer.render(url(sites, "/form"), trigger="always", timeout=6)
    assert after.title == "배송 조회"


def test_parse_connect():
    assert parse_connect(b"CONNECT Fake.Test.:443 HTTP/1.1\r\n\r\n") == ("fake.test", 443)
    assert parse_connect(b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n") == ("::1", 443)
    assert parse_connect(b"GET http://a/ HTTP/1.1\r\n\r\n") is None
    assert parse_connect(b"CONNECT a:99999 HTTP/1.1\r\n\r\n") is None


def stage2(html, status=200, failures=()):
    return CollectResult(
        input_url="https://a/", final_url="https://a/", redirect_chain=(), status_code=status,
        content_type="text/html", html=html, title=None, elapsed_ms=1, failures=failures,
    )


@pytest.mark.parametrize("html,status,failures,expected", [
    ("<p>" + "배송 안내 " * 60 + "</p>", 200, (), None),
    ('<div id="app"></div><script src="/app.js"></script>', 200, (), "script_only_page"),
    ("<p>" + "안내 " * 100 + "</p><script>window.location.href='/x'</script>", 200, (), "script_redirect"),
    ('<meta http-equiv="refresh" content="0;url=/x"><p>' + "안내 " * 100, 200, (), "script_redirect"),
    ("<p>not found</p>", 404, (), "status_403_404"),
    ('<script src="/a.js"></script>', 200, ("timeout",), None),
    ('<script src="/a.js"></script>', 200, ("html_truncated",), "script_only_page"),
    ("", 200, (), None),
    ("<p>" + "안내 " * 100 + "</p><script>var geolocation = 1; if (a == location) {}</script>", 200, (), None),
])
def test_browser_trigger(html, status, failures, expected):
    assert browser_trigger(stage2(html, status, failures)) == expected
