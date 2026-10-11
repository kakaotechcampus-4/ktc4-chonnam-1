#!/bin/bash
set -euo pipefail

# Preserve SSH replies and link-local DHCP/time services; new application egress stays closed.
# collector.env 가 있으면 수집 API 경로 두 개만 연다:
#   Render(RENDER_CIDRS) → 컨테이너 8443, 컨테이너 → TEST_PAGE_IP:443
# 없으면 기존처럼 컨테이너 전달을 모두 막는다.
COLLECTOR_BRIDGE=ktc-collector
if [ -f /opt/ktc-isolation/collector.env ]; then
    # shellcheck disable=SC1091
    . /opt/ktc-isolation/collector.env
fi
iptables -N KTC-INPUT 2>/dev/null || true
iptables -F KTC-INPUT
# 컨테이너에서 호스트 서비스(sshd, dockerd 등)로 가는 새 연결은 모두 막는다.
iptables -A KTC-INPUT -i "$COLLECTOR_BRIDGE" -j DROP
iptables -A KTC-INPUT -i lo -j ACCEPT
iptables -A KTC-INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A KTC-INPUT -p tcp --dport 22 -j ACCEPT
# 연결 시험용 스텁(stub_api.py)만 호스트에서 8443을 직접 받는다. 수집기(collector.env)가
# 설정되면 KTC-FORWARD 가 Render 대역만 허용하므로, 우회로가 되지 않게 이 규칙을 넣지 않는다.
if [ ! -f /opt/ktc-isolation/collector.env ]; then
    iptables -A KTC-INPUT -p tcp --dport 8443 -j ACCEPT
fi
iptables -A KTC-INPUT -p udp --sport 67 --dport 68 -j ACCEPT
iptables -A KTC-INPUT -j DROP
iptables -C INPUT -j KTC-INPUT 2>/dev/null || iptables -I INPUT 1 -j KTC-INPUT

iptables -N KTC-OUTPUT 2>/dev/null || true
iptables -F KTC-OUTPUT
iptables -A KTC-OUTPUT -o lo -j ACCEPT
iptables -A KTC-OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A KTC-OUTPUT -p udp --sport 68 --dport 67 -j ACCEPT
iptables -A KTC-OUTPUT -p udp -d 169.254.169.123 --dport 123 -j ACCEPT
iptables -A KTC-OUTPUT -j DROP
iptables -C OUTPUT -j KTC-OUTPUT 2>/dev/null || iptables -I OUTPUT 1 -j KTC-OUTPUT

iptables -N KTC-FORWARD 2>/dev/null || true
iptables -F KTC-FORWARD
if [ -n "${TEST_PAGE_IP:-}" ] && [ -n "${RENDER_CIDRS:-}" ]; then
    iptables -A KTC-FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    for cidr in $RENDER_CIDRS; do
        iptables -A KTC-FORWARD -o "$COLLECTOR_BRIDGE" -s "$cidr" -p tcp --dport 8443 \
            -m conntrack --ctstate NEW -j RETURN
    done
    iptables -A KTC-FORWARD -i "$COLLECTOR_BRIDGE" -d "$TEST_PAGE_IP" -p tcp --dport 443 \
        -m conntrack --ctstate NEW -j RETURN
    # 감시 신호(§5.12): 차단 패킷 수는 iptables -L KTC-FORWARD -v -n 으로 본다.
    iptables -A KTC-FORWARD -i "$COLLECTOR_BRIDGE" -m limit --limit 6/min \
        -j LOG --log-prefix "ktc-collector-drop "
fi
iptables -A KTC-FORWARD -j DROP
iptables -C FORWARD -j KTC-FORWARD 2>/dev/null || iptables -I FORWARD 1 -j KTC-FORWARD
if iptables -S DOCKER-USER >/dev/null 2>&1; then
    iptables -C DOCKER-USER -j KTC-FORWARD 2>/dev/null || iptables -I DOCKER-USER 1 -j KTC-FORWARD
fi

for chain in INPUT OUTPUT FORWARD; do
    ip6tables -N "KTC-$chain" 2>/dev/null || true
    ip6tables -F "KTC-$chain"
    if [ "$chain" != FORWARD ]; then
        ip6tables -A "KTC-$chain" -i lo -j ACCEPT 2>/dev/null || ip6tables -A "KTC-$chain" -o lo -j ACCEPT
    fi
    ip6tables -A "KTC-$chain" -j DROP
    ip6tables -C "$chain" -j "KTC-$chain" 2>/dev/null || ip6tables -I "$chain" 1 -j "KTC-$chain"
done
