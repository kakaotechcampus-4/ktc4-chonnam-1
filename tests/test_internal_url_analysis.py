"""Attempted destinations never become observed final URLs or official confirmation."""

from dataclasses import replace

import pytest

import main
from scanner.collect_response import parse_collect_response
from scanner.isolation_page import to_isolated_page
from scanner.models import BrowserResult, CollectResult


OFFICIAL = "https://www.cjlogistics.com/"
OFFICIAL_PAGE = "https://www.cjlogistics.com/en/main"
EVIL = "https://parcel-attacker.example/"
SHORT = "https://bit.ly/test"


def collected(input_url=OFFICIAL, **changes):
    return replace(CollectResult(input_url, OFFICIAL, (), 200, "text/html", "<p>조회</p>", None, 1), **changes)


def browser(final_url, chain, failures=(), downloads=(), requests=None):
    requests = chain if requests is None else requests
    return BrowserResult("script_redirect", final_url, chain, "<p>조회</p>", None, downloads, 0, 1, failures, requests)


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
    (collected(browser=browser(OFFICIAL, (OFFICIAL,))), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(browser=browser(OFFICIAL_PAGE, (OFFICIAL, OFFICIAL_PAGE))), OFFICIAL_PAGE, "www.cjlogistics.com", "official"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL,), downloads=("a.apk",))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, ())), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(EVIL, (OFFICIAL,))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(EVIL, browser=browser(OFFICIAL, (EVIL, OFFICIAL))), OFFICIAL, "parcel-attacker.example", "brand_mismatch"),
    (collected(browser=browser(EVIL, (OFFICIAL, EVIL))), EVIL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL, EVIL, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    # 이동 요청 기록: 확정되지 않은 HTTP 경유까지 대조한다
    (collected(browser=browser(OFFICIAL, (OFFICIAL,), requests=(OFFICIAL,))), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL,), requests=(OFFICIAL, EVIL, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL,), requests=())), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    (collected(browser=browser(OFFICIAL, (OFFICIAL,), requests=(OFFICIAL, "not-a-url"))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
    # 브라우저도 입력 주소에서 출발하므로 앞쪽 단축 주소는 경유로 치지 않는다. 뒤쪽 단축 주소는 친다
    (collected(SHORT, redirect_chain=(SHORT, OFFICIAL), browser=browser(OFFICIAL, (OFFICIAL,), requests=(SHORT, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "official"),
    (collected(SHORT, redirect_chain=(SHORT, OFFICIAL), browser=browser(OFFICIAL, (OFFICIAL,), requests=(SHORT, OFFICIAL, SHORT, OFFICIAL))), OFFICIAL, "www.cjlogistics.com", "unresolved"),
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


@pytest.mark.parametrize("input_url, domain, official", [
    (SHORT, "bit.ly", "unresolved"),
    (OFFICIAL, "www.cjlogistics.com", "unresolved"),
])
def test_browser_commits_hide_js_target_http_redirect(input_url, domain, official):
    # JS visits EVIL, which returns HTTP 302 to OFFICIAL. framenavigated only
    # records committed documents, so EVIL is only in navigation_requests (ADR 0009).
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
            "navigation_requests": [input_url, EVIL, OFFICIAL],
        },
    })

    analysis = main.build_ai_url_analysis(result, "CJ대한통운", input_url)

    assert analysis.official.value == official
    assert analysis.domain == domain
    assert analysis.final_url == OFFICIAL
    page, failure = to_isolated_page(result)
    assert page.info == risk_html
    assert failure is None


HANJIN = "https://www.hanjin.com/"


@pytest.mark.parametrize("brand", [None, "unknown"])
def test_official_domain_without_brand_in_message_is_official(brand):
    # #164: 문자에 '한진' 이 없어도 목록의 공식 주소면 공식 대조를 한다
    result = collected(HANJIN, final_url=HANJIN)
    assert main.build_ai_url_analysis(result, brand, HANJIN).official.value == "official"


@pytest.mark.parametrize("brand", [None, "unknown"])
def test_brand_from_domain_never_becomes_mismatch(brand):
    # 주소로 찾은 기관은 사칭(brand_mismatch) 근거가 아니다. 목록에 없는 주소는 지금처럼 대조 보류
    result = collected(EVIL, final_url=EVIL)
    assert main.build_ai_url_analysis(result, brand, EVIL).official.value == "not_registered"


def test_brand_from_domain_still_demotes_on_foreign_redirect():
    result = collected(HANJIN, final_url=EVIL, redirect_chain=(EVIL,))
    assert main.build_ai_url_analysis(result, None, HANJIN).official.value == "unresolved"


def test_message_brand_still_wins_over_domain():
    # 문자가 CJ대한통운이라 했는데 주소가 한진이면 지금처럼 사칭 패턴이다
    result = collected(HANJIN, final_url=HANJIN)
    assert main.build_ai_url_analysis(result, "CJ대한통운", HANJIN).official.value == "brand_mismatch"


COUPANG_SHORT = "https://coupa.ng/cbL6qT"
COUPANG = "https://www.coupang.com/np/fresh-bag"


def test_coupang_short_link_landing_on_coupang_is_official():
    # #161 1번: 쿠팡 단축 주소가 쿠팡 공식 주소로 이동하면 공식 대조
    result = collected(COUPANG_SHORT, final_url=COUPANG, redirect_chain=(COUPANG_SHORT, COUPANG))
    analysis = main.build_ai_url_analysis(result, "쿠팡", COUPANG_SHORT)
    assert analysis.domain == "www.coupang.com"
    assert analysis.official.value == "official"


def test_coupang_short_link_landing_elsewhere_is_not_official():
    result = collected(COUPANG_SHORT, final_url=EVIL, redirect_chain=(COUPANG_SHORT, EVIL))
    assert main.build_ai_url_analysis(result, "쿠팡", COUPANG_SHORT).official.value == "brand_mismatch"


def test_university_link_without_brand_is_official():
    # #161 2번: 문자에 기관 이름이 없어도 대학 공식 주소면 공식 대조(#164 와 같은 방식)
    jnu = "https://international.jnu.ac.kr/"
    result = collected(jnu, final_url=jnu)
    assert main.build_ai_url_analysis(result, None, jnu).official.value == "official"
