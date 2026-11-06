"""Configuration validation happens before any public firewall rules are emitted."""

import json
import os
import runpy
import sys
import subprocess
from pathlib import Path

import pytest

ISOLATION = Path(__file__).resolve().parents[1] / "infra" / "isolation"
ROOT = ISOLATION.parents[1]
sys.path.insert(0, str(ISOLATION))
sys.path.insert(0, str(ROOT))


def env(**changes):
    return dict(COLLECTOR_NETWORK_MODE="public", COLLECTOR_DNS_SERVER="1.1.1.1",
                COLLECTOR_PROTECTED_HOSTS="api.service.example",
                COLLECTOR_PROTECTED_IPS="8.8.8.8,8.8.4.4",
                RENDER_CIDRS="192.0.2.0/24", **changes)


def test_public_firewall_drops_internal_and_protected_before_443(tmp_path):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    rules = module["firewall_rules"](tmp_path, env())
    public_allow = rules.index('-i ktc-collector -p tcp --dport 443')
    for target in ("10.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10", "224.0.0.0/4", "8.8.8.8", "8.8.4.4"):
        assert rules.index(f"-d {target} -j DROP") < public_allow
    assert "-d 1.1.1.1 -p udp --dport 53" in rules
    assert "-d 1.1.1.1 -p tcp --dport 53" in rules
    assert "--dport 80" not in rules
    assert "-o ktc-collector -s 192.0.2.0/24" in rules


def test_protected_render_ip_can_receive_only_inbound_api_replies(tmp_path):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    config = env()
    config["RENDER_CIDRS"] = "8.8.8.0/24"
    rules = module["firewall_rules"](tmp_path, config).splitlines()
    reply_rule = (
        "-A KTC-FORWARD -i ktc-collector -d 8.8.8.0/24 -p tcp --sport 8443 "
        "-m conntrack --ctstate ESTABLISHED --ctdir REPLY "
        "--ctorigsrc 8.8.8.0/24 --ctorigdstport 8443 -j RETURN"
    )
    assert reply_rule in rules
    protected_drop = "-A KTC-FORWARD -i ktc-collector -d 8.8.8.8 -j DROP"
    private_drop = "-A KTC-FORWARD -i ktc-collector -d 10.0.0.0/8 -j DROP"
    established = "-A KTC-FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"
    assert rules.index(reply_rule) < rules.index(protected_drop) < rules.index(established)
    assert rules.index(reply_rule) < rules.index(private_drop)
    # Original-direction collector flows still meet the protected DROP before any allowance.
    exemptions = [rule for rule in rules[:rules.index(protected_drop)] if "8.8.8.0/24" in rule]
    assert exemptions == [reply_rule]


@pytest.mark.parametrize("key,value", [
    ("COLLECTOR_NETWORK_MODE", "publik"), ("COLLECTOR_PROTECTED_HOSTS", ""),
    ("COLLECTOR_PROTECTED_IPS", ""), ("COLLECTOR_PROTECTED_IPS", "not-an-ip"),
    ("COLLECTOR_DNS_SERVER", ""), ("COLLECTOR_DNS_SERVER", "1.1.1.1; echo BAD"),
    ("COLLECTOR_DNS_SERVER", "169.254.169.254"),
    ("RENDER_CIDRS", "invalid"), ("RENDER_CIDRS", ""),
])
def test_invalid_public_config_emits_no_rules(tmp_path, key, value):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    config = env()
    config[key] = value
    with pytest.raises(ValueError):
        module["firewall_rules"](tmp_path, config)


def chains(ruleset):
    rules = {}
    for line in ruleset.splitlines():
        if line.startswith("-A "):
            rules.setdefault(line.split()[1], []).append(line)
    return rules


def run_network_config(*args, **env_vars):
    return subprocess.run(
        [sys.executable, str(ISOLATION / "network_config.py"), *args],
        capture_output=True, text=True, cwd=ROOT,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT), **env_vars},
    )


@pytest.mark.parametrize("config", [env(), {}])
def test_ruleset_replaces_every_chain_at_once_and_ends_each_in_drop(tmp_path, config):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    ruleset = module["firewall_rules"](tmp_path, config)
    lines = ruleset.splitlines()
    assert lines[0] == "*filter" and lines[-1] == "COMMIT"
    for chain in ("KTC-INPUT", "KTC-OUTPUT", "KTC-FORWARD"):
        assert f":{chain} - [0:0]" in lines
        assert chains(ruleset)[chain][-1] == f"-A {chain} -j DROP"
    assert chains(ruleset)["KTC-INPUT"][0] == "-A KTC-INPUT -i ktc-collector -j DROP"
    assert not any("8443" in rule for rule in chains(ruleset)["KTC-INPUT"])
    # iptables-restore only understands double quotes.
    assert "'" not in ruleset


def test_rejected_configuration_prints_no_ruleset(tmp_path):
    config = env()
    config["COLLECTOR_DNS_SERVER"] = "169.254.169.254"
    result = run_network_config(str(tmp_path), **config)
    assert result.returncode == 1
    assert result.stdout == ""


def test_fail_closed_ruleset_forwards_nothing_and_keeps_ssh(tmp_path):
    result = run_network_config("--fail-closed")
    assert result.returncode == 0, result.stderr
    rules = chains(result.stdout)
    assert rules["KTC-FORWARD"] == ["-A KTC-FORWARD -j DROP"]
    assert "-A KTC-INPUT -p tcp --dport 22 -j ACCEPT" in rules["KTC-INPUT"]
    assert rules["KTC-INPUT"][0] == "-A KTC-INPUT -i ktc-collector -j DROP"
    normal = runpy.run_path(str(ISOLATION / "network_config.py"))["firewall_rules"](tmp_path, env())
    assert rules["KTC-OUTPUT"] == chains(normal)["KTC-OUTPUT"]
    assert result.stdout.splitlines()[-1] == "COMMIT"


def test_seccomp_profile_denies_io_uring():
    profile = json.loads((ISOLATION / "seccomp.json").read_text())
    allowed = {name for rule in profile["syscalls"] if rule["action"] == "SCMP_ACT_ALLOW"
               for name in rule.get("names", [])}
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert not {name for name in allowed if name.startswith("io_uring")}


def test_verification_reports_protected_hosts_resolving_elsewhere():
    module = runpy.run_path(str(ISOLATION / "verify.py"))
    outside = module["addresses_outside"]
    assert outside(["8.8.8.8", "8.8.4.4"], frozenset({"8.8.8.8", "8.8.4.4"})) == []
    assert outside(["8.8.8.8", "9.9.9.9"], frozenset({"8.8.8.8"})) == ["9.9.9.9"]


def test_verification_counts_days_until_certificate_expiry():
    module = runpy.run_path(str(ISOLATION / "verify.py"))
    now = 1_800_000_000.0
    not_after = "Jan 30 08:00:00 2027 GMT"
    days = module["days_until_expiry"](not_after, now)
    import ssl
    assert days == (ssl.cert_time_to_seconds(not_after) - now) / 86400


def test_allowlist_firewall_keeps_only_registered_ips(tmp_path):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    (tmp_path / "egress-ips.txt").write_text("93.184.216.34\n")
    rules = module["firewall_rules"](tmp_path, {"RENDER_CIDRS": "192.0.2.0/24"})
    assert "-d 93.184.216.34 -p tcp --dport 443" in rules
    assert "--dport 53" not in rules
    assert "-i ktc-collector -p tcp --dport 443" not in rules


def test_public_compose_override_has_explicit_dns_and_no_stale_hosts(tmp_path, monkeypatch):
    for key, value in env().items():
        monkeypatch.setenv(key, value)
    module = runpy.run_path(str(ISOLATION / "render-egress.py"))
    assert module["main"](str(tmp_path)) == 0
    compose = (tmp_path / "compose.egress.yaml").read_text(encoding="utf-8")
    assert "1.1.1.1" in compose
    assert 'COLLECTOR_NETWORK_MODE: "public"' in compose
    assert "extra_hosts" not in compose


def test_public_verification_probes_protected_ips(monkeypatch):
    for key, value in env().items():
        monkeypatch.setenv(key, value)
    module = runpy.run_path(str(ISOLATION / "verify.py"))
    targets = module["blocked_probe_addresses"]()
    assert "8.8.8.8" in targets
    assert "169.254.169.254" in targets
    assert "1.1.1.1" not in targets


def test_host_configuration_has_no_third_party_imports():
    result = subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import network_config", str(ISOLATION)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
