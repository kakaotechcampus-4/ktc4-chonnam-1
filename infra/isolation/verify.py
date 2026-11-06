"""Run inside the offline browser container: python /app/verify.py."""
import ctypes
import json
import errno
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path

from collector.policy import NetworkPolicy, parse_list


IO_URING_SETUP_X86_64 = 425


def public_policy():
    if os.getenv("COLLECTOR_NETWORK_MODE", "allowlist") != "public":
        return None
    return NetworkPolicy(
        mode="public",
        protected_hosts=parse_list(os.getenv("COLLECTOR_PROTECTED_HOSTS", "")),
        protected_ips=parse_list(os.getenv("COLLECTOR_PROTECTED_IPS", "")),
    )


def blocked_probe_addresses():
    targets = ("169.254.169.254", "10.0.1.1")
    policy = public_policy()
    if policy:
        return (*targets, *sorted(policy.protected_ips))
    return (*targets, "1.1.1.1")


def addresses_outside(answers, protected):
    return [address for address in dict.fromkeys(answers) if address not in protected]


def days_until_expiry(not_after, now):
    return (ssl.cert_time_to_seconds(not_after) - now) / 86400


def check_kernel_surface():
    libc = ctypes.CDLL(None, use_errno=True)
    params = ctypes.create_string_buffer(120)
    assert libc.syscall(IO_URING_SETUP_X86_64, 1, params) == -1, "io_uring_setup is allowed"
    assert ctypes.get_errno() in (errno.EPERM, errno.ENOSYS), ctypes.get_errno()
    probe = Path("/tmp/exec-probe.sh")
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o700)
    try:
        subprocess.run([str(probe)], check=False)
    except PermissionError:
        pass
    else:
        raise AssertionError("/tmp allows exec")
    finally:
        probe.unlink()


def check_certificate_and_protected_hosts(policy):
    not_after = ssl._ssl._test_decode_cert("/run/secrets/collector_cert")["notAfter"]
    assert days_until_expiry(not_after, time.time()) > 14, f"TLS certificate expires {not_after}"
    if policy is None:
        return
    for host in sorted(policy.protected_hosts):
        answers = [info[4][0] for info in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)]
        outside = addresses_outside(answers, policy.protected_ips)
        assert not outside, f"{host} resolves outside COLLECTOR_PROTECTED_IPS: {outside}"


def main():
    from playwright.sync_api import sync_playwright
    assert os.getuid() == 10001, "browser must not run as root"
    status = Path("/proc/self/status").read_text()
    assert "CapEff:\t0000000000000000" in status
    assert "NoNewPrivs:\t1" in status
    assert "Seccomp:\t2" in status
    assert not Path("/var/run/docker.sock").exists()
    assert not any(name.startswith("AWS_") or name.endswith("_API_KEY") or name in {"ISOLATION_TOKEN", "KAKAO_SKILL_SECRET"} for name in os.environ)
    try:
        Path("/home/collector/write-probe").write_text("forbidden")
    except OSError as error:
        assert error.errno == errno.EROFS, error
    else:
        raise AssertionError("root filesystem is writable")
    check_kernel_surface()
    check_certificate_and_protected_hosts(public_policy())
    for address in blocked_probe_addresses():
        try:
            socket.create_connection((address, 443), timeout=1).close()
        except OSError:
            pass
        else:
            raise AssertionError(f"network unexpectedly reachable: {address}")
    # 수집 API용 브리지 하나만 있어야 한다. 바깥으로 나가는 길은 lockdown.sh 가 egress-ips.txt 의 IP:443 만 연다.
    assert set(os.listdir("/sys/class/net")) <= {"lo", "eth0"}, "unexpected network interface present"
    token = Path("/run/secrets/collect_token").read_text().strip()
    assert token not in "".join(os.environ.values()), "collect token leaked into environment"
    browser_env = {name: value for name, value in os.environ.items() if name in {"PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH"}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium", chromium_sandbox=True, env=browser_env)
        first = browser.new_context(accept_downloads=False, service_workers="block")
        page = first.new_page()
        page.set_content("<title>Offline check</title><p>ready</p>", timeout=6000)
        assert page.title() == "Offline check"
        sandbox_page = first.new_page()
        sandbox_page.goto("chrome://sandbox", timeout=6000)
        sandbox_status = sandbox_page.locator("body").inner_text()
        assert "You are adequately sandboxed." in sandbox_status, sandbox_status
        first.add_cookies([{"name": "probe", "value": "1", "url": "https://offline.invalid"}])
        first.close()
        second = browser.new_context(accept_downloads=False, service_workers="block")
        assert second.cookies() == [], "cookies survived a new context"
        second.close()
        browser.close()
        # 수집 API 는 기본 headless shell 을 쓴다. 같은 방식으로 띄워 렌더러가 샌드박스 안인지 본다.
        shell = playwright.chromium.launch(chromium_sandbox=True, env=browser_env)
        shell_page = shell.new_page()
        shell_page.set_content("<title>Shell check</title>", timeout=6000)
        renderers = [cmd for cmd in _cmdlines() if b"--type=renderer" in cmd]
        assert renderers, "no renderer process found"
        assert not any(b"--no-sandbox" in cmd for cmd in renderers), "renderer runs without sandbox"
        shell_version = shell.version
        shell.close()
        print(json.dumps({"result": "passed", "chromium": shell_version, "sandbox": sandbox_status}))


def _cmdlines():
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                yield (proc / "cmdline").read_bytes()
            except OSError:
                continue


if __name__ == "__main__":
    main()
