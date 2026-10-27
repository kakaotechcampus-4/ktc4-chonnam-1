#!/usr/bin/env python3
"""노트북에서 실행한다. egress-hosts 의 고정 IP 가 지금 DNS 와 맞는지 확인한다.

    python3 infra/isolation/check-egress-ips.py egress-hosts          # 확인
    python3 infra/isolation/check-egress-ips.py --print egress-hosts  # 지금 IP 로 채운 내용 출력

고정 IP 가 지금 DNS 결과에 없으면 바뀐 것이다. --print 결과로 파일을 갱신하고
EC2 에 올린 뒤 build.sh 를 다시 실행한다. 보안 그룹 아웃바운드 규칙도 함께 바꾼다.
"""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from egress_hosts import EgressHostsError, parse_egress_hosts  # noqa: E402


def current_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def main(argv: list[str]) -> int:
    print_mode = "--print" in argv
    paths = [a for a in argv if a != "--print"]
    if len(paths) != 1:
        print(__doc__)
        return 2
    text = Path(paths[0]).read_text(encoding="utf-8")
    try:
        # --print 는 아직 문서용 IP 가 들어 있는 예시 파일에서도 돌 수 있어야 한다.
        entries = parse_egress_hosts(text, require_public=not print_mode)
    except EgressHostsError as exc:
        print(f"egress-hosts 오류: {exc}", file=sys.stderr)
        return 1

    changed = 0
    for host, pinned in entries:
        ips = current_ips(host)
        if print_mode:
            print(f"{host:<32} {ips[0] if ips else 'RESOLVE_FAILED'}")
            continue
        if not ips:
            print(f"[해석 실패] {host}")
            changed += 1
        elif pinned not in ips:
            print(f"[바뀜]     {host}: 고정 {pinned} → 지금 {', '.join(ips)}")
            changed += 1
        else:
            print(f"[같음]     {host}: {pinned}")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
