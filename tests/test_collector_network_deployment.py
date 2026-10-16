"""Configuration validation happens before any public firewall rules are emitted."""

import runpy
import sys
import subprocess
from pathlib import Path

import pytest

ISOLATION = Path(__file__).resolve().parents[1] / "infra" / "isolation"
sys.path.insert(0, str(ISOLATION))
sys.path.insert(0, str(ISOLATION.parents[1]))


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


@pytest.mark.parametrize("key,value", [
    ("COLLECTOR_NETWORK_MODE", "publik"), ("COLLECTOR_PROTECTED_HOSTS", ""),
    ("COLLECTOR_PROTECTED_IPS", ""), ("COLLECTOR_PROTECTED_IPS", "not-an-ip"),
    ("COLLECTOR_DNS_SERVER", ""), ("COLLECTOR_DNS_SERVER", "1.1.1.1; echo BAD"),
    ("RENDER_CIDRS", "invalid"), ("RENDER_CIDRS", ""),
])
def test_invalid_public_config_emits_no_rules(tmp_path, key, value):
    module = runpy.run_path(str(ISOLATION / "network_config.py"))
    config = env()
    config[key] = value
    with pytest.raises(ValueError):
        module["firewall_rules"](tmp_path, config)


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
