"""브라우저 전용 나가는 연결 검사 프록시 (docs/isolation-security.md §5.2·§5.14).

Playwright `route`는 서버 리다이렉트를 따라간 요청을 다시 보여주지 않는다.
"허용 주소 → 허용 주소 → 내부 주소" 2단 리다이렉트가 route 검사를 우회해 내부 서버까지
요청이 간 것을 로컬에서 재현했다. 그래서 브라우저의 모든 연결을 이 프록시로 보내고
연결마다 목적지를 확인한다. Playwright 는 프록시를 쓰면 루프백 주소도 프록시로 보낸다.

- CONNECT 만 받는다. 평문 HTTP 는 응답 없이 끊는다. 한 연결로 여러 호스트에 요청할 수 있어
  검사가 새고, 403 을 돌려주면 크롬이 그 응답을 대상 페이지 내용으로 보여 주기 때문이다
- 목적지 호스트·포트를 허용 함수로 확인하고, 허용될 때만 터널을 연다
- DNS 는 이 프로세스가 푼다(/etc/hosts 의 extra_hosts). 브라우저는 DNS 를 하지 않는다
- 헤더 크기·대기 시간·동시 터널 수·터널 수명을 제한한다

네트워크 방화벽(TEST_PAGE_IP:443 만 허용)이 최종 방어선이고, 이 프록시는 그 앞의 앱 계층이다.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

log = logging.getLogger("collector.proxy")

MAX_HEADER_BYTES = 8192
HEADER_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 5.0
TUNNEL_LIFETIME_SECONDS = 30.0
MAX_TUNNELS = 16

_FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def parse_connect(head: bytes) -> tuple[str, int] | None:
    """`CONNECT host:port HTTP/1.1` 이면 (host, port), 아니면 None."""
    parts = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
    if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1."):
        return None
    host, sep, port = parts[1].rpartition(":")
    host = host.strip("[]").lower().rstrip(".")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    return host, int(port)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class EgressProxy:
    def __init__(self, allow: Callable[[str, int], bool]) -> None:
        self._allow = allow
        self._server: asyncio.Server | None = None
        self._tunnels = 0
        self.blocked = 0  # 거부한 연결 수. 렌더러가 작업 전후 차이로 작업별 횟수를 센다
        self.port: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle, "127.0.0.1", 0, limit=MAX_HEADER_BYTES
            )
            self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            if hasattr(server, "close_clients"):  # Python 3.13+
                server.close_clients()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), 1.0)

    async def _deny(self, writer: asyncio.StreamWriter, target: tuple[str, int] | None) -> None:
        self.blocked += 1
        # 경로·쿼리는 남기지 않는다. CONNECT 면 호스트만, 평문 HTTP 면 "-".
        log.info("event=proxy_blocked host=%s", target[0] if target else "-")
        if target is not None:
            writer.write(_FORBIDDEN)
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_SECONDS)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            target = parse_connect(head)
            if target is None or not self._allow(*target):
                await self._deny(writer, target)
                return
            if self._tunnels >= MAX_TUNNELS:
                writer.write(_BAD_GATEWAY)
                await writer.drain()
                return
            self._tunnels += 1
            try:
                try:
                    up_reader, upstream = await asyncio.wait_for(
                        asyncio.open_connection(*target), CONNECT_TIMEOUT_SECONDS
                    )
                except (OSError, TimeoutError):
                    writer.write(_BAD_GATEWAY)
                    await writer.drain()
                    return
                writer.write(_ESTABLISHED)
                await writer.drain()
                await asyncio.wait_for(
                    asyncio.gather(_pipe(reader, upstream), _pipe(up_reader, writer)),
                    TUNNEL_LIFETIME_SECONDS,
                )
            finally:
                self._tunnels -= 1
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            for w in (upstream, writer):
                if w is not None:
                    with contextlib.suppress(Exception):
                        w.close()
