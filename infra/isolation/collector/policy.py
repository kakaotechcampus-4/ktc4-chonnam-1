"""Collector allowlist / opt-in public HTTPS policy, enforced again at socket connect."""

import asyncio
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
HOST_PATTERN = re.compile(r"(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+")
Resolver = Callable[[str], Awaitable[list[str]]]


async def default_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def load_allowed_hosts(raw: str | None = None) -> frozenset[str]:
    """`COLLECTOR_ALLOWED_HOSTS`(쉼표 구분)를 소문자·끝 점 제거한 집합으로 읽는다."""
    raw = os.getenv("COLLECTOR_ALLOWED_HOSTS", "") if raw is None else raw
    return frozenset(
        host.strip().lower().rstrip(".") for host in raw.split(",") if host.strip()
    )


def is_allowed_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    """https, 포트 443, 허용 호스트일 때만 True.

    해석이 단계마다 달라질 수 있는 URL(사용자 정보 `user@`, 백슬래시, 공백·제어 문자)은
    호스트가 맞아 보여도 거부한다. 숫자·16진수 IP 표기는 허용 목록의 호스트명과
    일치하지 않으므로 따로 처리하지 않아도 거부된다.
    """
    if not url or len(url) > MAX_URL_LENGTH or "\\" in url:
        return False
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".")
    return host in allowed_hosts


@dataclass(frozen=True)
class NetworkPolicy:
    """Public DNS is checked again inside CONNECT; only its checked IPv4 is dialed."""

    mode: str = "allowlist"
    allowed_hosts: frozenset[str] = frozenset()
    protected_hosts: frozenset[str] = frozenset()
    protected_ips: frozenset[str] = frozenset()
    resolve: Resolver = default_resolve

    def __post_init__(self) -> None:
        if self.mode not in ("allowlist", "public"):
            raise ValueError("COLLECTOR_NETWORK_MODE must be allowlist or public")
        if self.mode == "allowlist":
            if not self.allowed_hosts:
                raise ValueError("allowlist is empty")
            return
        if not self.protected_hosts or not self.protected_ips:
            raise ValueError("public mode requires protected hosts and IPv4 addresses")
        if any(not HOST_PATTERN.fullmatch(host) for host in self.protected_hosts):
            raise ValueError("invalid protected hostname")
        for address in self.protected_ips:
            if ipaddress.ip_address(address).version != 4:
                raise ValueError("protected addresses must be IPv4")

    def _public_ip(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return False
        return (ip.is_global and not ip.is_multicast and not ip.is_reserved
                and str(ip) not in self.protected_ips)

    def host_allowed(self, host: str, port: int) -> bool:
        host = host.lower().rstrip(".")
        if port != 443:
            return False
        if self.mode == "allowlist":
            return host in self.allowed_hosts
        if any(host == protected or host.endswith("." + protected) for protected in self.protected_hosts):
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # Reject legacy numeric IPv4 spellings accepted by libc / Chromium.
            try:
                socket.inet_aton(host)
            except OSError:
                return bool(HOST_PATTERN.fullmatch(host)) and not host.rsplit(".", 1)[-1].isdigit()
            return False
        return ip.version == 4 and self._public_ip(host)

    def url_allowed(self, url: str) -> bool:
        if self.mode == "allowlist":
            return is_allowed_url(url, self.allowed_hosts)
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").encode("idna").decode("ascii").rstrip(".")
            return (is_allowed_url(url, frozenset({parts.hostname.rstrip(".")}))
                    and self.host_allowed(host, parts.port or 443))
        except (ValueError, UnicodeError, AttributeError):
            return False

    async def connect_ip(self, host: str, port: int) -> str:
        if not self.host_allowed(host, port):
            raise ValueError("blocked target")
        if self.mode == "allowlist":
            return host
        try:
            try:
                ipaddress.ip_address(host)
                addresses = [host]
            except ValueError:
                addresses = await self.resolve(host)
            if not addresses or any(not self._public_ip(address) for address in addresses):
                raise ValueError("blocked DNS answer")
            return next(address for address in addresses if ipaddress.ip_address(address).version == 4)
        except (OSError, ValueError, StopIteration) as exc:
            raise ValueError("no permitted IPv4 address") from exc
