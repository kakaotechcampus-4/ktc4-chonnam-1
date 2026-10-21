#!/usr/bin/env python3
"""EC2 에서 build.sh 가 실행한다. egress-hosts 로 두 파일을 만든다.

- compose.egress.yaml: 컨테이너 /etc/hosts 고정(extra_hosts)과 수집기 허용 호스트
- egress-ips.txt: lockdown.sh 가 443 을 열어 줄 IP 목록

검증에 실패하면 아무 파일도 바꾸지 않고 끝난다.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from egress_hosts import EgressHostsError, parse_egress_hosts  # noqa: E402
from network_config import public_config  # noqa: E402

HEADER = "# render-egress.py 가 egress-hosts 로 만든 파일이다. 직접 고치지 않는다.\n"


def render(entries: list[tuple[str, str]]) -> tuple[str, str]:
    hosts = ",".join(host for host, _ in entries)
    lines = [HEADER, "services:\n", "  browser:\n", "    extra_hosts:\n"]
    lines += [f'      - "{host}:{ip}"\n' for host, ip in entries]
    lines += ["    environment:\n", f'      COLLECTOR_ALLOWED_HOSTS: "{hosts}"\n']
    ips = sorted({ip for _, ip in entries}, key=lambda ip: tuple(int(p) for p in ip.split(".")))
    return "".join(lines), "".join(f"{ip}\n" for ip in ips)


def main(directory: str = "/opt/ktc-isolation") -> int:
    base = Path(directory)
    try:
        mode = os.getenv("COLLECTOR_NETWORK_MODE", "allowlist")
        if mode == "public":
            policy, dns = public_config(os.environ)
            compose = HEADER + "services:\n  browser:\n    dns:\n      - " + json.dumps(dns) + "\n    environment:\n"
            values = dict(COLLECTOR_NETWORK_MODE="public",
                          COLLECTOR_PROTECTED_HOSTS=",".join(sorted(policy.protected_hosts)),
                          COLLECTOR_PROTECTED_IPS=",".join(sorted(policy.protected_ips)))
            compose += "".join(f"      {key}: {json.dumps(value)}\n" for key, value in values.items())
            ips = ""
            entries = []
        elif mode == "allowlist":
            entries = parse_egress_hosts((base / "egress-hosts").read_text(encoding="utf-8"))
            compose, ips = render(entries)
        else:
            raise ValueError("invalid COLLECTOR_NETWORK_MODE")
    except (OSError, ValueError) as exc:
        print(f"egress-hosts 오류: {exc}", file=sys.stderr)
        return 1
    (base / "compose.egress.yaml").write_text(compose, encoding="utf-8")
    (base / "egress-ips.txt").write_text(ips, encoding="utf-8")
    print(f"허용 호스트 {len(entries)}개, IP {len(ips.splitlines())}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
