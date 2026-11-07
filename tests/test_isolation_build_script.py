"""build.sh 는 systemd 가 새로 만든 컨테이너에서 verify.py 를 돌린다.

docker 와 systemctl 을 가짜 명령으로 바꿔, `systemctl restart` 가 돌아온 뒤에야
compose 가 옛 컨테이너를 지우고 새 컨테이너를 띄우는 EC2 의 순서를 흉내 낸다.
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

state = Path(__file__).resolve().parent.parent / "container"
log = state.parent / "exec.log"
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
    with log.open("a") as f:
        f.write(container + "\n")
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if current() != container:
            sys.exit(137)
        time.sleep(0.05)
    print('{"result": "passed"}')
'''

FAKE_SYSTEMCTL = r'''#!/bin/bash
root=$(cd "$(dirname "$0")/.." && pwd)
if [ "$1" = restart ] && [ "$2" = ktc-isolation-collector ]; then
    ( sleep 0.5; rm -f "$root/container"; sleep 0.5; echo new-container > "$root/container" ) >/dev/null 2>&1 &
fi
exit 0
'''


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_build(tmp_path, old_container):
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
    if old_container:
        (tmp_path / "container").write_text(old_container)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(work / "build.sh")], env=env,
                            capture_output=True, text=True, timeout=90)
    log = tmp_path / "exec.log"
    return result, log.read_text().split() if log.exists() else []


@pytest.mark.parametrize("old_container", ["old-container", None])
def test_verify_runs_in_the_container_systemd_just_created(tmp_path, old_container):
    result, exec_targets = run_build(tmp_path, old_container)
    assert result.returncode == 0, result.stderr
    assert exec_targets == ["new-container"]
