"""Attempted destinations never become observed final URLs or official confirmation."""

from dataclasses import replace

import pytest

import main
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
    (collected(browser=browser(EVIL, (OFFICIAL, EVIL))), EVIL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL, EVIL, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser("about:blank", (), ("browser_timeout",))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(EVIL, (OFFICIAL,), ("browser_timeout",))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(SHORT, final_url=SHORT, browser=browser(OFFICIAL, (SHORT, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(SHORT, redirect_chain=(OFFICIAL,), browser=browser(OFFICIAL, (SHORT, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "official"),
])
def test_collected_addresses_drive_domain_comparison(result, final_url, domain, official):
    analysis = main.build_ai_url_analysis(result, "CJ대한통운", result.input_url)
    assert analysis.final_url == final_url
    assert analysis.domain == domain
    assert analysis.official.value == official
    assert analysis.scan.score is None
    assert analysis.scan.scanned_at is None
