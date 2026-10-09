"""Run inside the offline browser container: python /app/verify.py."""
import json
import errno
import os
import socket
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
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
    for address in ("169.254.169.254", "10.0.1.1", "1.1.1.1"):
        try:
            socket.create_connection((address, 443), timeout=1).close()
        except OSError:
            pass
        else:
            raise AssertionError(f"network unexpectedly reachable: {address}")
    # 수집 API용 브리지 하나만 있어야 한다. 바깥으로 나가는 길은 lockdown.sh 가 TEST_PAGE_IP:443 만 연다.
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
