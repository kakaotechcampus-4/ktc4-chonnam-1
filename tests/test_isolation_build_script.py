"""build.sh 는 배포마다 컨테이너를 한 번만 새로 만들고, 그 컨테이너에서 verify.py 를 돌린다.

docker 와 systemctl 을 가짜 명령으로 바꿔 EC2 의 systemd 동작을 흉내 낸다.
- 수집기 유닛은 Requires= 로 방화벽 유닛에 묶여, 돌고 있을 때 방화벽을 다시 시작하면
  함께 다시 시작한다. ExecStop 의 compose down 이 옛 컨테이너를 먼저 지운다.
  이 전파가 없다고 가정한 경우도 함께 본다.
- 꺼진 유닛을 시작하면 compose up --force-recreate 가 손으로 띄운 옛 컨테이너를
  systemctl 이 돌아온 뒤에 지운다.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ISOLATION = Path(__file__).resolve().parents[1] / "infra" / "isolation"

FAKE_DOCKER = r'''#!/usr/bin/env python3
import sys, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
state = root / "container"
args = sys.argv[1:]

def current():
    return state.read_text().strip() if state.exists() else ""

if args[:2] == ["image", "inspect"]:
    print("sha256:approved")
elif args[0] == "inspect":
    container = current()
    if not container:
        sys.exit(1)
    fmt = args[args.index("--format") + 1]
    print(fmt.replace("{{.Id}}", container).replace("{{.State.Running}}", "true"))
elif args[0] == "exec":
    container = current()
    if not container:
        sys.exit(1)
    with (root / "events.log").open("a") as f:
        f.write(f"exec {container}\n")
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if current() != container:
            sys.exit(137)
        time.sleep(0.05)
    print('{"result": "passed"}')
'''

FAKE_SYSTEMCTL = r'''#!/bin/bash
root=$(cd "$(dirname "$0")/.." && pwd)
log() { echo "$1" >> "$root/events.log"; }
create_later() {
    n=$(( $(cat "$root/count" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$root/count"
    ( sleep 1; echo "new-$n" > "$root/container"; log "create new-$n" ) >/dev/null 2>&1 &
}
start_inactive() {
    touch "$root/active"
    ( sleep 0.5; rm -f "$root/container" ) >/dev/null 2>&1 &
    create_later
}
[ -f "$root/broken" ] && exit 0
case "$1 $2" in
    "stop ktc-isolation-collector")
        rm -f "$root/active"
        [ -f "$root/was-active" ] && rm -f "$root/container" ;;
    "restart ktc-isolation-firewall")
        if [ -f "$root/active" ] && [ -f "$root/propagates" ]; then rm -f "$root/container"; create_later; fi ;;
    "restart ktc-isolation-collector")
        if [ -f "$root/active" ]; then rm -f "$root/container"; create_later; else start_inactive; fi ;;
    "start ktc-isolation-collector")
        [ -f "$root/active" ] || start_inactive ;;
    "start ktc-isolation-recreate.timer")
        log timers ;;
esac
exit 0
'''

FAKE_TIMEOUT = '#!/bin/bash\nshift\nexec "$@"\n'
FAKE_SLEEP = "#!/bin/bash\nexit 0\n"


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_build(tmp_path, *, old_container, unit_active, propagates=True, broken=False):
    work = tmp_path / "opt"
    for sub in ("scanner", "collector"):
        (work / sub).mkdir(parents=True)
        (work / sub / "x.py").write_text("")
    for name in ("seccomp.json", "verify.py", "collector.env"):
        (work / name).write_text("")
    (work / "Dockerfile").write_text("ARG BASE_IMAGE=base@sha256:0\n")
    (work / "render-egress.py").write_text(
        "import sys, pathlib\n"
        "for name in ('compose.egress.yaml', 'egress-ips.txt'):\n"
        "    (pathlib.Path(sys.argv[1]) / name).write_text('')\n"
    )
    script = (ISOLATION / "build.sh").read_text().replace("/opt/ktc-isolation", str(work))
    (work / "build.sh").write_text(script)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "docker", FAKE_DOCKER)
    executable(bin_dir / "systemctl", FAKE_SYSTEMCTL)
    executable(bin_dir / "timeout", FAKE_TIMEOUT)
    if broken:
        executable(bin_dir / "sleep", FAKE_SLEEP)
        (tmp_path / "broken").touch()
    if old_container:
        (tmp_path / "container").write_text(old_container)
    if unit_active:
        (tmp_path / "active").touch()
        (tmp_path / "was-active").touch()
    if propagates:
        (tmp_path / "propagates").touch()

    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    result = subprocess.run(["bash", str(work / "build.sh")], env=env,
                            capture_output=True, text=True, timeout=90)
    events = tmp_path / "events.log"
    return result, events.read_text().splitlines() if events.exists() else []


@pytest.mark.parametrize(("old_container", "unit_active", "propagates"), [
    pytest.param("old-container", False, True, id="manual-container"),
    pytest.param("old-container", True, True, id="unit-managed"),
    pytest.param("old-container", True, False, id="unit-managed-without-restart-propagation"),
    pytest.param(None, False, True, id="fresh-host"),
])
def test_one_new_container_is_verified_before_timers_start(tmp_path, old_container, unit_active, propagates):
    result, events = run_build(tmp_path, old_container=old_container, unit_active=unit_active,
                               propagates=propagates)
    assert result.returncode == 0, result.stderr
    assert events == ["create new-1", "exec new-1", "timers"]


def test_no_new_container_fails_without_verifying_the_old_one(tmp_path):
    result, events = run_build(tmp_path, old_container="old-container", unit_active=True, broken=True)
    assert result.returncode == 1
    assert "old-container" in result.stderr
    assert "build.sh" in result.stderr
    assert not any(event.startswith("exec") for event in events)
    assert "timers" not in events
