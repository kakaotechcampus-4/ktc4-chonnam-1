"""Validate deployment inputs before emitting any shell firewall commands (stdlib only)."""

import ipaddress
import os
import shlex
import sys
from pathlib import Path

from collector.policy import NetworkPolicy, load_allowed_hosts

# Deliberately includes special-purpose ranges even where Python versions differ.
BLOCKED_CIDRS = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
)


def public_config(env):
    policy = NetworkPolicy(
        mode=env.get("COLLECTOR_NETWORK_MODE", "allowlist"),
        protected_hosts=load_allowed_hosts(env.get("COLLECTOR_PROTECTED_HOSTS", "")),
        protected_ips=load_allowed_hosts(env.get("COLLECTOR_PROTECTED_IPS", "")),
    )
    dns = str(ipaddress.IPv4Address(env.get("COLLECTOR_DNS_SERVER", "")))
    address = ipaddress.ip_address(dns)
    if (dns in policy.protected_ips or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback):
        raise ValueError("DNS must be an explicit resolver, distinct from protected services")
    return policy, dns


def firewall_rules(base: Path, env) -> str:
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
            return ""
        ips = [str(ipaddress.IPv4Address(line)) for line in ips_file.read_text().splitlines() if line.strip()]
        if any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
            raise ValueError("allowlist requires public IPv4 addresses")
        if not ips:
            return ""

    rules = []

    def add(*args):
        rules.append(shlex.join(("iptables", "-A", "KTC-FORWARD", *args)))

    if mode == "public":
        for protocol in ("udp", "tcp"):
            add("-i", "ktc-collector", "-d", dns, "-p", protocol, "--dport", "53", "-j", "RETURN")
        for target in (*BLOCKED_CIDRS, *sorted(policy.protected_ips)):
            add("-i", "ktc-collector", "-d", target, "-j", "DROP")
    add("-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN")
    for cidr in cidrs:
        add("-o", "ktc-collector", "-s", cidr, "-p", "tcp", "--dport", "8443",
            "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    if mode == "public":
        add("-i", "ktc-collector", "-p", "tcp", "--dport", "443",
            "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    else:
        for ip in ips:
            add("-i", "ktc-collector", "-d", ip, "-p", "tcp", "--dport", "443",
                "-m", "conntrack", "--ctstate", "NEW", "-j", "RETURN")
    add("-i", "ktc-collector", "-m", "limit", "--limit", "6/min", "-j", "LOG",
        "--log-prefix", "ktc-collector-drop ")
    return "\n".join(rules) + "\n"


if __name__ == "__main__":
    try:
        print(firewall_rules(Path(sys.argv[1]), os.environ), end="")
    except (ValueError, OSError) as exc:
        print(f"invalid network configuration: {exc}", file=sys.stderr)
        sys.exit(1)
