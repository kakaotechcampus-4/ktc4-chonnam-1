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
    assert set(os.listdir("/sys/class/net")) == {"lo"}, "external network interface present"
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
        print(json.dumps({"result": "passed", "chromium": browser.version, "sandbox": sandbox_status}))
        browser.close()


if __name__ == "__main__":
    main()
