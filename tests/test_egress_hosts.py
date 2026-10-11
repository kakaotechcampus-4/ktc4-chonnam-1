"""격리 서버 접속 허용 목록(egress-hosts) 검증과 파일 생성."""

import runpy
import sys
from pathlib import Path

import pytest

ISOLATION = Path(__file__).resolve().parents[1] / "infra" / "isolation"
sys.path.insert(0, str(ISOLATION))

from egress_hosts import EgressHostsError, parse_egress_hosts  # noqa: E402

render_module = runpy.run_path(str(ISOLATION / "render-egress.py"), run_name="render_egress")
check_module = runpy.run_path(str(ISOLATION / "check-egress-ips.py"), run_name="check_egress")


def test_parse_ok():
    text = "# 주석\nBit.ly.  67.199.248.10  # 끝 주석\nnaver.me 223.130.200.107\n"
    assert parse_egress_hosts(text) == [("bit.ly", "67.199.248.10"), ("naver.me", "223.130.200.107")]


@pytest.mark.parametrize("line", [
    "bit.ly 10.0.1.5",            # 사설
    "bit.ly 169.254.169.254",     # 메타데이터
    "bit.ly 127.0.0.1",           # 루프백
    "bit.ly 203.0.113.20",        # 문서용 (예시 파일 그대로)
    "bit.ly 2606:4700::1",        # IPv6
    "bit.ly not-an-ip",
    "bit_ly 67.199.248.10",       # 호스트 형식
    "bit.ly",                     # 칸 부족
])
def test_parse_rejects(line):
    with pytest.raises(EgressHostsError):
        parse_egress_hosts(line)


def test_duplicate_and_empty():
    with pytest.raises(EgressHostsError):
        parse_egress_hosts("bit.ly 67.199.248.10\nbit.ly 67.199.248.11")
    with pytest.raises(EgressHostsError):
        parse_egress_hosts("# 비어 있음\n")


def test_example_file_must_be_filled():
    with pytest.raises(EgressHostsError):
        parse_egress_hosts((ISOLATION / "egress-hosts.example").read_text(encoding="utf-8"))


def test_render_writes_compose_and_ips(tmp_path):
    (tmp_path / "egress-hosts").write_text("bit.ly 67.199.248.10\nme2.do 67.199.248.10\nnaver.me 223.130.200.107\n")
    assert render_module["main"](str(tmp_path)) == 0
    compose = (tmp_path / "compose.egress.yaml").read_text()
    assert '- "bit.ly:67.199.248.10"' in compose
    assert 'COLLECTOR_ALLOWED_HOSTS: "bit.ly,me2.do,naver.me"' in compose
    assert (tmp_path / "egress-ips.txt").read_text() == "67.199.248.10\n223.130.200.107\n"


def test_render_fails_without_touching_files(tmp_path):
    (tmp_path / "egress-hosts").write_text("bit.ly 10.0.0.1\n")
    assert render_module["main"](str(tmp_path)) == 1
    assert not (tmp_path / "compose.egress.yaml").exists()


def test_check_reports_changed_ip(tmp_path, monkeypatch, capsys):
    path = tmp_path / "egress-hosts"
    path.write_text("bit.ly 67.199.248.10\nnaver.me 223.130.200.107\n")
    ips = {"bit.ly": ["67.199.248.10", "67.199.248.11"], "naver.me": ["223.130.195.200"]}
    monkeypatch.setitem(check_module["main"].__globals__, "current_ips", lambda host: ips[host])
    assert check_module["main"]([str(path)]) == 1
    out = capsys.readouterr().out
    assert "[같음]     bit.ly" in out and "[바뀜]     naver.me" in out


def test_shortener_lists_match():
    # 메인 서버 단축 목록과 격리 서버 예시 목록이 어긋나면 그 단축 주소는 늘 판단 보류가 된다.
    from services.link_resolution import SHORTENER_HOSTS

    text = (ISOLATION / "egress-hosts.example").read_text(encoding="utf-8")
    hosts = {line.split()[0] for line in text.splitlines() if line.strip() and not line.startswith("#")}
    assert SHORTENER_HOSTS <= hosts
