"""Public collection fails closed and connects only to the checked IPv4."""

import asyncio
import sys
import ssl
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

ISOLATION = Path(__file__).resolve().parents[1] / "infra" / "isolation"
sys.path.insert(0, str(ISOLATION))
sys.path.insert(0, str(ISOLATION.parents[1]))

from collector.app import create_app
from collector.egress_proxy import EgressProxy
from collector import policy as policy_module
from scanner.models import CollectResult
from scanner.fetch import collect_url

TOKEN = "t" * 40


def policy(**changes):
    return policy_module.NetworkPolicy(
        mode="public", protected_hosts=frozenset({"api.service.example"}),
        protected_ips=frozenset({"8.8.8.8"}), **changes,
    )


@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "2130706433", "0x7f000001", "0177.0.0.1",
    "::ffff:127.0.0.1", "api.service.example", "sub.api.service.example",
    "8.8.8.8", "224.0.0.1", "100.64.0.1",
])
async def test_blocked_target_never_resolves(host):
    async def resolve(_):
        pytest.fail("blocked target reached DNS")

    with pytest.raises(ValueError):
        await policy(resolve=resolve).connect_ip(host, 443)


@pytest.mark.parametrize("addresses", [
    [], ["93.184.216.34", "10.0.0.1"], ["93.184.216.34", "::ffff:127.0.0.1"],
    ["93.184.216.34", "8.8.8.8"], ["224.0.0.1"], ["garbage"], ["2606:4700::1"],
])
async def test_dns_answers_fail_closed(addresses):
    async def resolve(_):
        return addresses

    with pytest.raises(ValueError):
        await policy(resolve=resolve).connect_ip("unregistered.example", 443)


async def test_dns_failure_is_denied():
    async def resolve(_):
        raise OSError("DNS unavailable")

    with pytest.raises(ValueError):
        await policy(resolve=resolve).connect_ip("unregistered.example", 443)


@pytest.mark.parametrize("url", [
    "http://public.example/", "https://public.example:8443/", "https://user@public.example/",
    "https://public.example\\@other.example/", "https://public.example/\n",
])
def test_public_url_parser_rejects_unsupported_or_ambiguous_urls(url):
    assert not policy().url_allowed(url)


@pytest.mark.parametrize("changes", [
    {"mode": "typo"}, {"protected_hosts": frozenset()},
    {"protected_ips": frozenset()}, {"protected_ips": frozenset({"invalid"})},
    {"protected_hosts": frozenset({"https://api.service.example"})},
])
def test_public_config_requires_explicit_protections(changes):
    kwargs = dict(mode="public", protected_hosts=frozenset({"api.service.example"}),
                  protected_ips=frozenset({"8.8.8.8"}))
    kwargs.update(changes)
    with pytest.raises(ValueError):
        policy_module.NetworkPolicy(**kwargs)


async def test_proxy_dials_checked_ip_not_hostname(monkeypatch):
    resolved = []
    dialed = []

    async def resolve(host):
        resolved.append(host)
        return ["93.184.216.34"]

    async def dial(host, port):
        dialed.append((host, port))
        raise OSError("no actual internet in test")

    proxy = EgressProxy(lambda *_: True, connect_ip=policy(resolve=resolve).connect_ip)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        monkeypatch.setattr(asyncio, "open_connection", dial)
        writer.write(b"CONNECT Unregistered.Example:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert b"502" in await reader.read()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
    assert resolved == ["unregistered.example"]
    assert dialed == [("93.184.216.34", 443)]


async def test_public_api_uses_proxy_and_disables_insecure_retry():
    captured = {}

    async def collect(url, **kwargs):
        captured.update(kwargs)
        return CollectResult(input_url=url, final_url=url, redirect_chain=(), status_code=200,
                             content_type="text/html", html="ok", title=None, elapsed_ms=1, failures=())

    app = create_app(token=TOKEN, allowed_hosts=frozenset(), network_policy=policy(), collect=collect)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://collector") as c:
            response = await c.post("/collect", json={"url": "https://unregistered.example/"},
                                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.json()["failures"] == []
    assert captured["allow_insecure_retry"] is False
    client = captured["client"]
    assert client.is_closed
    assert client._mounts  # HTTPX installs the explicitly configured proxy transport.
    assert captured["url_allowed"]("https://another-public.example/")
    assert not captured["url_allowed"]("https://api.service.example/")


@pytest.mark.parametrize("target", ["10.0.0.1", "api.service.example", "mixed.example"])
async def test_connect_rejects_dns_or_redirect_targets_before_dial(target, monkeypatch):
    async def resolve(_):
        return ["93.184.216.34", "10.0.0.1"]

    p = policy(resolve=resolve)
    proxy = EgressProxy(p.host_allowed, connect_ip=p.connect_ip)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)

        async def forbidden(*_):
            pytest.fail("denied address reached socket connect")

        monkeypatch.setattr(asyncio, "open_connection", forbidden)
        writer.write(f"CONNECT {target}:443 HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        assert b"403" in await reader.read()
        writer.close()
        await writer.wait_closed()
        assert proxy.blocked == 1
    finally:
        await proxy.close()


async def test_proxy_close_cancels_open_tunnels():
    proxy = EgressProxy(lambda *_: True)
    await proxy.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"CONNECT")  # Pending header read must also be cleaned up.
    await writer.drain()
    await asyncio.sleep(0)
    await proxy.close()
    assert not proxy._handlers
    assert await asyncio.wait_for(reader.read(), 0.2) == b""
    writer.close()
    await writer.wait_closed()


async def test_httpx_real_tls_tunnel_and_redirect_keep_pinned_ip(tmp_path, monkeypatch):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl unavailable for local verified TLS origin")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
                    "-subj", "/CN=unregistered.example", "-addext",
                    "subjectAltName=DNS:unregistered.example", "-keyout", str(key),
                    "-out", str(cert)], check=True, capture_output=True)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    seen = []
    dials = []

    async def origin(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head.split(b"\r\n")[0])
        writer.write(b"HTTP/1.1 302 Found\r\nLocation: https://10.0.0.1/secret\r\n"
                     b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def resolve(_):
        return ["93.184.216.34"]

    server = await asyncio.start_server(origin, "127.0.0.1", 0, ssl=tls)
    p = policy(resolve=resolve)
    proxy = EgressProxy(p.host_allowed, connect_ip=p.connect_ip)
    await proxy.start()
    real_connect = asyncio.open_connection

    async def pinned_connect(host, port):
        dials.append((host, port))
        assert host == "93.184.216.34" and port == 443
        return await real_connect("127.0.0.1", server.sockets[0].getsockname()[1])

    monkeypatch.setattr(asyncio, "open_connection", pinned_connect)
    try:
        async with httpx.AsyncClient(proxy=proxy.url, trust_env=False,
                                     verify=ssl.create_default_context(cafile=str(cert))) as client:
            result = await collect_url("https://unregistered.example/start", client=client,
                                       resolve=resolve, url_allowed=p.url_allowed,
                                       allow_insecure_retry=False)
        assert result.failures == ("blocked_address",)
        assert seen == [b"GET /start HTTP/1.1"]
        assert dials == [("93.184.216.34", 443)]
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()
