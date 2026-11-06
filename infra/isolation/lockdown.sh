#!/bin/bash
set -euo pipefail

# Preserve SSH replies and link-local DHCP/time services; new application egress stays closed.
# collector.env 와 egress-ips.txt 가 있으면 수집 API 경로만 연다:
#   Render(RENDER_CIDRS) → 컨테이너 8443, 컨테이너 → egress-ips.txt 의 IP:443
# network_config.py 가 값을 모두 검증한 뒤 세 체인 전체를 한 묶음으로 내고, iptables-restore 가
# 한 번에 바꾼다. 검증이나 적용이 실패하면 전달을 모두 막는 규칙으로 바꾸고 실패로 끝낸다.
base=/opt/ktc-isolation

generate() {
    if [ -f "$base/collector.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "$base/collector.env" || return 1
        set +a
    fi
    python3 "$base/network_config.py" "$base"
}

status=0
if ! ruleset=$(generate) || ! printf '%s\n' "$ruleset" | iptables-restore --noflush; then
    python3 "$base/network_config.py" --fail-closed | iptables-restore --noflush
    status=1
fi

ip6tables-restore --noflush <<'EOF'
*filter
:KTC-INPUT - [0:0]
:KTC-OUTPUT - [0:0]
:KTC-FORWARD - [0:0]
-A KTC-INPUT -i lo -j ACCEPT
-A KTC-INPUT -j DROP
-A KTC-OUTPUT -o lo -j ACCEPT
-A KTC-OUTPUT -j DROP
-A KTC-FORWARD -j DROP
COMMIT
EOF

for chain in INPUT OUTPUT FORWARD; do
    iptables -C "$chain" -j "KTC-$chain" 2>/dev/null || iptables -I "$chain" 1 -j "KTC-$chain"
    ip6tables -C "$chain" -j "KTC-$chain" 2>/dev/null || ip6tables -I "$chain" 1 -j "KTC-$chain"
done
if iptables -S DOCKER-USER >/dev/null 2>&1; then
    iptables -C DOCKER-USER -j KTC-FORWARD 2>/dev/null || iptables -I DOCKER-USER 1 -j KTC-FORWARD
fi
exit "$status"
