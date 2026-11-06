"""Validate deployment inputs, then emit one iptables-restore ruleset for every KTC chain (stdlib only)."""

import ipaddress
import os
import sys
from pathlib import Path

from collector.policy import NetworkPolicy, parse_list

# Deliberately includes special-purpose ranges even where Python versions differ.
BLOCKED_CIDRS = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
)
BRIDGE = "ktc-collector"
INPUT_RULES = (
    # 컨테이너에서 호스트 서비스(sshd, dockerd 등)로 가는 새 연결은 모두 막는다.
    f"-i {BRIDGE} -j DROP",
    "-i lo -j ACCEPT",
    "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
    "-p tcp --dport 22 -j ACCEPT",
    "-p udp --sport 67 --dport 68 -j ACCEPT",
    "-j DROP",
)
OUTPUT_RULES = (
    "-o lo -j ACCEPT",
    "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
    "-p udp --sport 68 --dport 67 -j ACCEPT",
    "-p udp -d 169.254.169.123 --dport 123 -j ACCEPT",
    "-j DROP",
)


def ruleset(forward=()) -> str:
    """Declaring a chain in restore input flushes it, so COMMIT swaps all three at once."""
    lines = ["*filter", ":KTC-INPUT - [0:0]", ":KTC-OUTPUT - [0:0]", ":KTC-FORWARD - [0:0]"]
    lines += [f"-A KTC-INPUT {rule}" for rule in INPUT_RULES]
    lines += [f"-A KTC-OUTPUT {rule}" for rule in OUTPUT_RULES]
    lines += [f"-A KTC-FORWARD {rule}" for rule in (*forward, "-j DROP")]
    return "\n".join((*lines, "COMMIT")) + "\n"


def public_config(env):
    policy = NetworkPolicy(
        mode=env.get("COLLECTOR_NETWORK_MODE", "allowlist"),
        protected_hosts=parse_list(env.get("COLLECTOR_PROTECTED_HOSTS", "")),
        protected_ips=parse_list(env.get("COLLECTOR_PROTECTED_IPS", "")),
    )
    dns = str(ipaddress.IPv4Address(env.get("COLLECTOR_DNS_SERVER", "")))
    address = ipaddress.ip_address(dns)
    if (dns in policy.protected_ips or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback or address.is_link_local):
        raise ValueError("DNS must be an explicit resolver, distinct from protected services")
    return policy, dns


def firewall_rules(base: Path, env) -> str:
    return ruleset(forward_rules(base, env))


def forward_rules(base: Path, env) -> list[str]:
    mode = env.get("COLLECTOR_NETWORK_MODE", "allowlist")
    if mode not in ("public", "allowlist"):
        raise ValueError("invalid COLLECTOR_NETWORK_MODE")
    policy = dns = None
    if mode == "public":
        policy, dns = public_config(env)
        if not env.get("RENDER_CIDRS", "").strip():
            raise ValueError("public mode requires RENDER_CIDRS")
    cidrs = [str(ipaddress.IPv4Network(cidr)) for cidr in env.get("RENDER_CIDRS", "").split()]
    ips_file = base / "egress-ips.txt"
    ips = []
    if mode == "allowlist":
        if not ips_file.exists() or not cidrs:
            return []
        ips = [str(ipaddress.IPv4Address(line)) for line in ips_file.read_text().splitlines() if line.strip()]
        if any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
            raise ValueError("allowlist requires public IPv4 addresses")
        if not ips:
            return []

    rules = []

    def add(*args):
        rules.append(" ".join(args))

    if mode == "public":
        # Render may share a protected service IP; only replies to its inbound API flow pass.
        for cidr in cidrs:
            add("-i", BRIDGE, "-d", cidr, "-p", "tcp", "--sport", "8443",
                "-m", "conntrack", "--ctstate", "ESTABLISHED", "--ctdir", "REPLY",
                "--ctorigsrc", cidr, "--ctorigdstport", "8443", "-j", "RETURN")
        for protocol in ("udp", "tcp"):
            add("-i", BRIDGE, "-d", dns, "-p", protocol, "--dport", "53", "-j", "RETURN")
        for target in (*BLOCKED_CIDRS, *sorted(policy.protected_ips)):
            add("-i", BRIDGE, "-d", target, "-j", "DROP")
    add("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN")
    for cidr in cidrs:
        add("-o", BRIDGE, "-s", cidr, "-p", "tcp", "--dport", "8443",
            "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    if mode == "public":
        add("-i", BRIDGE, "-p", "tcp", "--dport", "443",
            "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    else:
        for ip in ips:
            add("-i", BRIDGE, "-d", ip, "-p", "tcp", "--dport", "443",
                "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    add("-i", BRIDGE, "-m", "limit", "--limit", "6/min", "-j", "LOG",
        "--log-prefix", "ktc-collector-drop:")
    return rules


if __name__ == "__main__":
    if sys.argv[1:] == ["--fail-closed"]:
        print(ruleset(), end="")
        sys.exit(0)
    try:
        print(firewall_rules(Path(sys.argv[1]), os.environ), end="")
    except (ValueError, OSError) as exc:
        print(f"invalid network configuration: {exc}", file=sys.stderr)
        sys.exit(1)
