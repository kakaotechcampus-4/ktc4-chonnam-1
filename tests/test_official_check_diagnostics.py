"""이슈 #104 진단 로그(`[OFFICIAL_CHECK]`) 검증.

`build_ai_url_analysis()`의 판정 로직 자체는 건드리지 않았다는 전제로,
각 강등 원인 코드가 올바른 조건에서만 찍히는지 확인한다. 기존
`test_internal_url_analysis.py`의 결과값(최종 official/도메인/URL)은
그대로이므로 여기서는 로그 내용만 본다.
"""

import logging
from dataclasses import replace

import main
from scanner.models import BrowserResult, CollectResult

OFFICIAL = "https://www.cjlogistics.com/"
EVIL = "https://parcel-attacker.example/"

_LOGGER_NAME = "official_check_diagnostics"


def collected(input_url=OFFICIAL, **changes):
    return replace(
        CollectResult(input_url, OFFICIAL, (), 200, "text/html", "<p>조회</p>", None, 1),
        **changes,
    )


def browser(final_url, chain, failures=()):
    return BrowserResult("script_redirect", final_url, chain, "<p>조회</p>", None, (), 0, 1, failures, chain)


def _run(caplog, result, brand="CJ대한통운"):
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        main.build_ai_url_analysis(result, brand, result.input_url)
    lines = [r.message for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(lines) == 1, "분석 1회당 [OFFICIAL_CHECK] 로그는 정확히 1줄이어야 한다"
    return lines[0]


def test_case1_official_url_fully_collected_logs_no_demotion(caplog):
    line = _run(caplog, collected())
    assert "initial_official=official" in line
    assert "final_official=official" in line
    assert "initial_check_failure=None" in line
    assert "demotion_reasons=[]" in line
    assert "browser_present=False" in line


def test_case2_official_only_browser_result_is_not_demoted(caplog):
    line = _run(caplog, collected(browser=browser(OFFICIAL, (OFFICIAL,))))
    assert "final_official=official" in line
    assert "demotion_reasons=[]" in line
    assert "browser_present=True" in line
    assert "browser_trigger=script_redirect" in line


def test_case2b_browser_failure_logs_browser_failure(caplog):
    line = _run(caplog, collected(browser=browser(OFFICIAL, (OFFICIAL,), ("browser_timeout",))))
    assert "final_official=unresolved" in line
    assert "'BROWSER_FAILURE'" in line


def test_case2c_non_official_hop_logs_reason_and_only_host_on_separate_line(caplog, capsys):
    hop = "https://parcel-attacker.example/track?phone=01012345678"
    line = _run(caplog, collected(browser=browser(OFFICIAL, (OFFICIAL, hop, OFFICIAL))))
    assert "final_official=unresolved" in line
    assert "'BROWSER_NON_OFFICIAL_HOP'" in line
    assert "parcel-attacker.example" not in line

    printed = capsys.readouterr().out
    assert "[OFFICIAL_HOP] demotion_host=parcel-attacker.example\n" in printed
    assert "01012345678" not in printed
    assert "/track" not in printed


def test_case3_missing_final_url_logs_final_url_missing(caplog):
    line = _run(
        caplog,
        collected(status_code=None, html="", failures=("connection_failed",)),
    )
    assert "final_official=unresolved" in line
    assert "'FINAL_URL_MISSING'" in line
    assert "final_url_present=False" in line


def test_case4_missing_html_logs_http_html_missing(caplog):
    line = _run(caplog, collected(html=""))
    assert "final_official=unresolved" in line
    assert "'HTTP_HTML_MISSING'" in line
    assert "http_html_present=False" in line
    # html이 비었을 뿐 final_url은 있다 — 다른 사유와 섞이지 않아야 한다
    assert "FINAL_URL_MISSING" not in line


def test_case5_collect_failure_logs_collect_failure(caplog):
    line = _run(caplog, collected(failures=("html_truncated",)))
    assert "final_official=unresolved" in line
    assert "'COLLECT_FAILURE'" in line
    assert "collect_failures=['html_truncated']" in line


def test_case6_redirect_chain_has_unofficial_host_logs_redirect_host_invalid(caplog):
    line = _run(caplog, collected(redirect_chain=(EVIL, OFFICIAL)))
    assert "final_official=unresolved" in line
    assert "'REDIRECT_HOST_INVALID'" in line
    assert "redirect_count=2" in line


def test_case7_initial_official_check_itself_fails(caplog):
    line = _run(caplog, collected(EVIL))
    assert "initial_official=brand_mismatch" in line
    assert "initial_check_failure=INITIAL_OFFICIAL_CHECK_FAILED" in line
    # 최초 판정부터 official이 아니면 "강등"은 일어나지 않는다
    assert "demotion_reasons=[]" in line


def test_case8_multiple_demotion_reasons_are_all_recorded_together(caplog):
    line = _run(
        caplog,
        collected(
            browser=browser(OFFICIAL, (OFFICIAL,), ("browser_timeout",)),
            html="",
            failures=("html_truncated",),
        ),
    )
    assert "final_official=unresolved" in line
    assert "'BROWSER_FAILURE'" in line
    assert "'HTTP_HTML_MISSING'" in line
    assert "'COLLECT_FAILURE'" in line
    assert "initial_check_failure=None" in line


def test_does_not_leak_full_url_or_host_into_log(caplog):
    """원인 코드·불리언·개수만 남기고 실제 URL/호스트 문자열은 남기지 않는다."""
    line = _run(caplog, collected(redirect_chain=(EVIL, OFFICIAL)))
    assert "parcel-attacker.example" not in line
    assert "cjlogistics.com" not in line
    assert "https://" not in line
