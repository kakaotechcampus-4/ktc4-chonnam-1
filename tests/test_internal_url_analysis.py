"""Attempted destinations never become observed final URLs or official confirmation."""

from dataclasses import replace

import pytest

import main
from scanner.collect_response import parse_collect_response
from scanner.isolation_page import to_isolated_page
from scanner.models import BrowserResult, CollectResult


OFFICIAL = "https://www.cjlogistics.com/"
EVIL = "https://parcel-attacker.example/"
SHORT = "https://bit.ly/test"


def collected(input_url=OFFICIAL, **changes):
    return replace(CollectResult(input_url, OFFICIAL, (), 200, "text/html", "<p>조회</p>", None, 1), **changes)


def browser(final_url, chain, failures=()):
    return BrowserResult("script_redirect", final_url, chain, "<p>조회</p>", None, (), 0, 1, failures)


@pytest.mark.parametrize("result, final_url, domain, official", [
    (collected(), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(EVIL), OFFICIAL, "parcel-attacker.example", "brand_mismatch"),
    (collected(final_url=EVIL, redirect_chain=(EVIL,)), EVIL, "www.cjlogistics.com", "unresolved"),
    (collected(redirect_chain=(EVIL, OFFICIAL)), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(redirect_chain=(SHORT, OFFICIAL)), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(SHORT, redirect_chain=(OFFICIAL, SHORT, OFFICIAL)), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(SHORT, redirect_chain=(EVIL, OFFICIAL)), OFFICIAL, "parcel-attacker.example", "brand_mismatch"),
    (collected(SHORT, redirect_chain=(OFFICIAL,)), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(SHORT, redirect_chain=(OFFICIAL,), status_code=None, html="", failures=("blocked_address",)), None, "www.cjlogistics.com", "unresolved"),
    (collected(final_url=EVIL, redirect_chain=(EVIL,), status_code=None, html="", failures=("timeout",)), None, "www.cjlogistics.com", "unresolved"),
    (collected(status_code=None, html="", failures=("connection_failed",)), None, "www.cjlogistics.com", "unresolved"),
    (collected(failures=("html_truncated",)), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(html=""), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL,))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(EVIL, browser=browser(OFFICIAL, (EVIL, OFFICIAL))), OFFICIAL, "parcel-attacker.example", "brand_mismatch"),
    (collected(browser=browser(EVIL, (OFFICIAL, EVIL))), EVIL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL, EVIL, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser("about:blank", (), ("browser_timeout",))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(EVIL, (OFFICIAL,), ("browser_timeout",))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(SHORT, final_url=SHORT, browser=browser(OFFICIAL, (SHORT, OFFICIAL))), OFFICIAL, "bit.ly", "unresolved"),
    (collected(SHORT, redirect_chain=(OFFICIAL,), browser=browser(OFFICIAL, (SHORT, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
])
def test_collected_addresses_drive_domain_comparison(result, final_url, domain, official):
    analysis = main.build_ai_url_analysis(result, "CJ대한통운", result.input_url)
    assert analysis.final_url == final_url
    assert analysis.domain == domain
    assert analysis.official.value == official
    assert analysis.scan.score is None
    assert analysis.scan.scanned_at is None


@pytest.mark.parametrize("input_url, domain", [
    (SHORT, "bit.ly"),
    (OFFICIAL, "www.cjlogistics.com"),
])
def test_browser_commits_hide_js_target_http_redirect_without_confirming_official(input_url, domain):
    # JS visits EVIL, which returns HTTP 302 to OFFICIAL. framenavigated only
    # records committed documents, so the collector never puts EVIL in this chain.
    risk_html = '<form><input type="password" name="password"></form>'
    result = parse_collect_response(input_url, {
        "input_url": input_url, "final_url": input_url, "redirect_chain": [],
        "status_code": 200, "content_type": "text/html", "title": None,
        "html": f'<script>location.href="{EVIL}"</script>', "elapsed_ms": 1,
        "failures": [],
        "browser": {
            "trigger": "script_redirect", "final_url": OFFICIAL,
            "navigation_chain": [input_url, OFFICIAL], "html": risk_html,
            "title": None, "downloads": [], "blocked_requests": 0,
            "elapsed_ms": 1, "failures": [],
        },
    })

    analysis = main.build_ai_url_analysis(result, "CJ대한통운", input_url)

    assert analysis.official.value == "unresolved"
    assert analysis.domain == domain
    assert analysis.final_url == OFFICIAL
    page, failure = to_isolated_page(result)
    assert page.info == risk_html
    assert failure is None
