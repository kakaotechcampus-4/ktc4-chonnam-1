"""egress-hosts 파일 읽기·검증. render-egress.py 와 check-egress-ips.py 가 함께 쓴다."""

import ipaddress
import re

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


class EgressHostsError(ValueError):
    pass


def parse_egress_hosts(text: str, *, require_public: bool = True) -> list[tuple[str, str]]:
    """`호스트 IP` 줄을 읽어 [(host, ip)] 로 돌려준다. 형식이 틀리면 EgressHostsError."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise EgressHostsError(f"{number}행: '호스트 IP' 형식이 아닙니다")
        host = parts[0].lower().rstrip(".")
        if not _HOST_RE.match(host):
            raise EgressHostsError(f"{number}행: 호스트 이름이 올바르지 않습니다: {parts[0]}")
        if host in seen:
            raise EgressHostsError(f"{number}행: 호스트가 중복됩니다: {host}")
        try:
            address = ipaddress.ip_address(parts[1])
        except ValueError as exc:
            raise EgressHostsError(f"{number}행: IP 주소가 아닙니다: {parts[1]}") from exc
        if address.version != 4:
            raise EgressHostsError(f"{number}행: IPv4 만 받습니다: {parts[1]}")
        # 사설·루프백·링크 로컬(메타데이터 169.254.169.254 포함)·문서용 주소를 막는다.
        if require_public and not address.is_global:
            raise EgressHostsError(f"{number}행: 공인 IP 가 아닙니다: {parts[1]}")
        seen.add(host)
        entries.append((host, str(address)))
    if not entries:
        raise EgressHostsError("허용 호스트가 하나도 없습니다")
    return entries
