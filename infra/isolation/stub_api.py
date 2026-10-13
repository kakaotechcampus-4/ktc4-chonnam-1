"""Connection-test stub for the isolation /collect API.

It never visits the requested URL. It only proves the path Render -> EC2:
TLS, Bearer token, request shape, and a response the BE parser accepts.
Secrets arrive through systemd LoadCredential, never through env or argv.
"""

import hmac
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY_BYTES = 8 * 1024
MAX_URL_LENGTH = 2048
CREDENTIALS = Path(os.environ["CREDENTIALS_DIRECTORY"])
TOKEN = (CREDENTIALS / "token").read_text(encoding="utf-8").strip().encode()
if len(TOKEN) < 32:
    raise SystemExit("token credential is missing or too short")


class StubHandler(BaseHTTPRequestHandler):
    server_version = "ktc-isolation-stub"
    sys_version = ""
    timeout = 10

    def log_message(self, format: str, *args: object) -> None:
        # Never log URLs, headers, or bodies; only method, path, and status.
        pass

    def log_request(self, code: object = "-", size: object = "-") -> None:
        print(f"{self.command} {self.path.split('?')[0]} {code}", flush=True)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme == "Bearer" and hmac.compare_digest(value.strip().encode(), TOKEN)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "mode": "stub"})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/collect":
            self._send_json(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(411, {"error": "length_required"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "body_too_large"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return
        url = data.get("url") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
            self._send_json(400, {"error": "invalid_url"})
            return
        self._send_json(200, {
            "input_url": url,
            "final_url": None,
            "redirect_chain": [],
            "status_code": None,
            "content_type": None,
            "html": "",
            "title": None,
            "elapsed_ms": 0,
            "failures": ["stub_not_collected"],
        })


def main() -> None:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CREDENTIALS / "cert", CREDENTIALS / "key")
    server = ThreadingHTTPServer(("0.0.0.0", 8443), StubHandler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
