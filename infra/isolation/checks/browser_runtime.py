"""Run as the non-root collector user in an isolated validation container."""
import asyncio
import json
import os
import ssl
import subprocess
import tempfile
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from collector.app import create_app_from_env
from collector.browser import BrowserRenderer
from collector.egress_proxy import EgressProxy


class Origin(ThreadingHTTPServer):
    daemon_threads = True
    connections = 0

    def process_request(self, request, address):
        self.connections += 1
        super().process_request(request, address)


class Pages(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        inside = self.server.inside
        if self.path == "/bounce":
            self.send_response(302)
            self.send_header("Location", f"https://www.cjlogistics.com:{self.server.server_port}/form")
            self.end_headers()
            return
        if self.path in ("/chain", "/chain2"):
            self.send_response(302)
            self.send_header("Location", "/chain2" if self.path == "/chain" else inside)
            self.end_headers()
            return
        if self.path == "/file.bin":
            self.send_response(200)
            self.send_header("Content-Disposition", 'attachment; filename="fixture.bin"')
            self.end_headers()
            self.wfile.write(b"inert fixture, never executable")
            return
        pages = {
            "/form": '<title>ready</title><form><input type="password"></form>',
            "/js": '<script>location.href="/form"</script>',
            "/outside": f'<title>blocked</title><script>location.href="{inside}"</script>',
            "/resources": f'<title>resources</title><iframe src="{inside}"></iframe>'
                f'<script>fetch("{inside}").catch(()=>{{}});'
                f'try{{new WebSocket("{inside.replace("http:", "ws:")}")}}catch(e){{}}</script>',
            "/download": '<a id="d" href="/file.bin">download</a>'
                '<script>document.getElementById("d").click()</script>',
            "/busy": '<script>while(true){}</script>',
            "/cloaked": '<script>location.href="https://attacker.example.test:'
                + str(self.server.server_port) + '/bounce"</script>',
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(pages.get(self.path, "fixture").encode())


async def check_local(directory):
    key, cert = directory / "key.pem", directory / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
        "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1", "-subj", "/CN=fixture",
        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    inside = Origin(("127.0.0.1", 0), Pages)
    inside.inside = "http://127.0.0.1:1/"
    public = Origin(("127.0.0.1", 0), Pages)
    public.inside = f"http://127.0.0.1:{inside.server_port}/private"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    public.socket = tls.wrap_socket(public.socket, server_side=True)
    for server in (inside, public):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"https://127.0.0.1:{public.server_port}"
    renderer = BrowserRenderer(lambda u: u.startswith(base + "/"),
        EgressProxy(lambda h, p: h == "127.0.0.1" and p == public.server_port),
        sandbox=True, ignore_https_errors=True)  # Self-signed loopback fixture only.
    checks = []
    try:
        await renderer.start()
        for path in ("/form", "/js"):
            result = await renderer.render(base + path, trigger="always", timeout=6)
            assert result.title == "ready" and not result.failures, (path, result.failures)
            checks.append(path)
        for path in ("/outside", "/chain", "/resources"):
            result = await renderer.render(base + path, trigger="always", timeout=6)
            assert inside.connections == 0, path
            assert result.blocked_requests > 0, path
            if path != "/resources":
                assert "browser_blocked_navigation" in result.failures, (path, result.failures)
            checks.append(path)
        result = await renderer.render(base + "/download", trigger="always", timeout=6)
        assert result.downloads == (base + "/file.bin",), result.downloads
        assert not list(Path("/tmp").rglob("fixture.bin")), "download was saved"
        checks.append("download_not_saved")
        result = await renderer.render(base + "/busy", trigger="always", timeout=2)
        assert "browser_timeout" in result.failures, result.failures
        result = await renderer.render(base + "/form", trigger="always", timeout=6)
        assert result.title == "ready" and not result.failures, result.failures
        checks.append("timeout_then_recovery")
        allowed = {"bit.ly", "attacker.example.test", "www.cjlogistics.com"}

        async def local_fixture_ip(host, port):
            assert host in allowed and port == public.server_port
            return "127.0.0.1"

        cloaking = BrowserRenderer(lambda u: any(u.startswith(f"https://{h}:{public.server_port}/") for h in allowed),
            EgressProxy(lambda h, p: h in allowed and p == public.server_port, connect_ip=local_fixture_ip),
            sandbox=True, ignore_https_errors=True)  # All three names point only to this TLS fixture.
        try:
            await cloaking.start()
            entry = f"https://bit.ly:{public.server_port}/cloaked"
            result = await cloaking.render(entry, trigger="script_redirect", timeout=6)
            assert result.final_url == f"https://www.cjlogistics.com:{public.server_port}/form", result.failures
            assert not result.failures, result.failures
            assert all("attacker.example.test" not in u for u in result.navigation_chain), result.navigation_chain
            print(json.dumps({"real_browser_hidden_hop": {"input_url": entry, "final_url": entry,
                "redirect_chain": [], "status_code": 200, "content_type": "text/html",
                "html": '<script>location.href="https://attacker.example.test/bounce"</script>',
                "title": None, "elapsed_ms": 0, "failures": [], "browser": asdict(result)}}))
            checks.append("hidden_http_redirect_reproduced")
        finally:
            await cloaking.close()
    finally:
        await renderer.close()
        for server in (inside, public):
            server.shutdown()
            server.server_close()
    print(json.dumps({"local_browser_checks": checks, "internal_connections": inside.connections}))


async def check_public():
    # Uses the real production factory: sandbox and HTTPS verification remain enabled.
    os.environ["COLLECTOR_NETWORK_MODE"] = "public"
    os.environ["COLLECTOR_PROTECTED_HOSTS"] = "image-evaluation-server.onrender.com,ktc4-cn1-api.duckdns.org"
    os.environ["COLLECTOR_PROTECTED_IPS"] = "3.39.166.84"
    os.environ["COLLECTOR_BROWSER"] = "always"
    app = create_app_from_env()
    token = Path(os.getenv("COLLECTOR_TOKEN_FILE", "/run/secrets/collect_token")).read_text().strip()
    url = "https://kiki03st.github.io/isolation-test-pages/v1/cases/col-01/default.html"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://check") as client:
            response = await client.post("/collect", json={"url": url})
            assert response.status_code == 401
            response = await client.post("/collect", json={"url": url},
                headers={"Authorization": "Bearer " + token})
            data = response.json()
            assert data["status_code"] == 200 and not data["failures"], data["failures"]
            browser = data["browser"]
            assert browser and browser["html"] and not browser["failures"], browser and browser["failures"]
            assert browser["final_url"] == url, browser["final_url"]
            print(json.dumps({"public_https_status": data["status_code"], "tls_verification": True,
                "browser_html_chars": len(browser["html"]), "browser_failures": browser["failures"],
                "elapsed_ms": data["elapsed_ms"] + browser["elapsed_ms"]}))
            for target in ("https://169.254.169.254/", "https://3.39.166.84/",
                           "https://image-evaluation-server.onrender.com/"):
                r = await client.post("/collect", json={"url": target},
                    headers={"Authorization": "Bearer " + token})
                assert r.json()["failures"] == ["blocked_address"], r.json()["failures"]
    print(json.dumps({"public_factory_auth_and_protected_targets": "passed"}))


async def main():
    assert os.getuid() == 10001
    with tempfile.TemporaryDirectory() as directory:
        await check_local(Path(directory))
    await check_public()


if __name__ == "__main__":
    asyncio.run(main())
