"""수집 결과를 공식 주소 목록과 대조해 `UrlAnalysis` 를 만든다.

링크 문자 판정의 근거다(CLAUDE.md 원칙 1). 처음 닿은 실제 호스트를 목록과 대조하고,
수집이 불완전하거나 도중에 공식이 아닌 곳을 거쳤으면 `unresolved` 로 내린다(ADR 0009).
"""

import logging
import sys
from urllib.parse import urlparse

from ai.types import UrlAnalysis
from scanner.models import BrowserResult, CollectResult
from services.link_resolution import first_real_host, is_shortener
from services.official_domain_service import brand_for_domain, check_link_official

# 이슈 #104 진단 로그 전용 로거. uvicorn/Render가 루트 로거에 핸들러를
# 붙이는 시점과 무관하게 항상 stdout에 찍히도록 핸들러를 직접 단다
# (print() 대신 logging을 쓰되, basicConfig()의 "루트에 핸들러가 이미
# 있으면 무시" 동작에 기대지 않는다).
_official_check_logger = logging.getLogger("official_check_diagnostics")
if not _official_check_logger.handlers:
    # 기존 print() 호출들과 같은 스트림(stdout)으로 보낸다.
    _official_check_handler = logging.StreamHandler(sys.stdout)
    _official_check_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _official_check_logger.addHandler(_official_check_handler)
_official_check_logger.setLevel(logging.INFO)
_official_check_logger.propagate = False


def _http_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return parsed.hostname if parsed.scheme in {"http", "https"} else None
    except ValueError:
        return None


def _log_official_check_diagnostics(
    *,
    initial_official: str,
    final_official: str,
    initial_check_failure: str | None,
    demotion_reasons: list[str],
    browser: BrowserResult | None,
    collected: CollectResult,
    final_url: str | None,
) -> None:
    """이슈 #104 진단 로그. 판정 로직 자체는 건드리지 않고 원인만 남긴다.

    `initial_check_failure`(최초 판정부터 official이 아니었던 경우)와
    `demotion_reasons`(official에서 unresolved로 강등된 사유)는 서로
    배타적이다 — 최초 판정이 official이 아니면 강등 자체가 일어나지
    않으므로 demotion_reasons는 항상 빈 목록이다. 둘을 같은 목록에
    섞지 않는다.

    사용자 메시지·전체 URL·쿼리 문자열·HTML 본문·호스트 문자열은
    남기지 않는다 — 원인 코드·불리언·개수·HTTP 상태 코드만 쓴다.
    """

    _official_check_logger.info(
        "[OFFICIAL_CHECK] initial_official=%s final_official=%s "
        "initial_check_failure=%s demotion_reasons=%s browser_present=%s "
        "browser_trigger=%s browser_failures=%s http_status=%s "
        "redirect_count=%d final_url_present=%s http_html_present=%s "
        "collect_failures=%s",
        initial_official,
        final_official,
        initial_check_failure,
        demotion_reasons,
        browser is not None,
        browser.trigger if browser is not None else None,
        list(browser.failures) if browser is not None else [],
        collected.status_code,
        len(collected.redirect_chain),
        bool(final_url),
        bool(collected.html),
        list(collected.failures),
    )


def _browser_demotion(
    browser: BrowserResult | None, brand: str | None, judged_domain: str | None,
) -> tuple[str | None, str | None]:
    """브라우저 결과가 공식 판정을 내릴 사유와 걸린 호스트를 돌려준다. 없으면 (None, None).

    브라우저가 돌았다는 사실만으로는 내리지 않는다. 스크립트로 이동하는 공식 사이트가 늘 판단 보류가 되기
    때문이다. 이동 요청 기록으로 HTTP 경유까지 대조한다(ADR 0009).
    """

    if browser is None:
        return None, None
    if browser.failures:
        return "BROWSER_FAILURE", None
    if browser.downloads:
        return "BROWSER_DOWNLOAD", None
    if not browser.navigation_chain or not browser.navigation_requests:
        return "BROWSER_CHAIN_EMPTY", None
    # 브라우저도 입력 주소에서 출발한다. redirect_chain 대조처럼 맨 앞의 단축 주소만 건너뛴다.
    hops = list(browser.navigation_requests)
    while hops and is_shortener(_http_host(hops[0])):
        hops.pop(0)
    for url in (*browser.navigation_chain, *hops, browser.final_url):
        host = _http_host(url)
        if host is None and url != browser.final_url:
            return "BROWSER_NON_OFFICIAL_HOP", None
        if host is not None and check_link_official(brand, judged_domain, host) != "official":
            return "BROWSER_NON_OFFICIAL_HOP", host
    return None, None


def build_ai_url_analysis(
    collected: CollectResult,
    brand: str | None,
    input_url: str,
) -> UrlAnalysis:
    """Compare the received/first real host; incomplete or later movement only downgrades."""
    browser = collected.browser
    judged_domain = first_real_host(input_url, collected.redirect_chain)
    if not brand or brand == "unknown":
        # #164: 문자에 기관 이름이 없으면 주소가 목록의 공식 도메인인지로만 대조한다.
        # 목록에 없는 주소면 None 그대로라 지금처럼 not_registered 다
        brand = brand_for_domain(judged_domain)

    # fetch.final_url may name an unvisited blocked/timeout destination.
    final_url = (collected.final_url
                 if collected.status_code is not None and _http_host(collected.final_url)
                 else None)
    if (browser and _http_host(browser.final_url)
            and browser.final_url in browser.navigation_chain):
        final_url = browser.final_url

    official = check_link_official(brand, judged_domain, _http_host(final_url))
    initial_official = official
    # 최초 판정이 official이 아닌 경우는 "강등"이 아니다 — 강등 사유
    # 목록과는 분리해서 남긴다(아래 _log_official_check_diagnostics 참고).
    initial_check_failure = (
        "INITIAL_OFFICIAL_CHECK_FAILED" if initial_official != "official" else None
    )
    demotion_reasons: list[str] = []
    if official == "official":
        browser_demotion, demotion_host = _browser_demotion(browser, brand, judged_domain)
        incomplete = (browser_demotion is not None or not final_url or not collected.html
                      or bool(collected.failures))
        if browser_demotion:
            demotion_reasons.append(browser_demotion)
            if demotion_host:
                # 화이트리스트 검토용. 경로·쿼리에는 개인정보가 있을 수 있어 호스트만 따로 남긴다
                _official_check_logger.info("[OFFICIAL_HOP] demotion_host=%s", demotion_host)
        if not final_url:
            demotion_reasons.append("FINAL_URL_MISSING")
        if not collected.html:
            demotion_reasons.append("HTTP_HTML_MISSING")
        if collected.failures:
            demotion_reasons.append("COLLECT_FAILURE")
        if incomplete:
            official = "unresolved"
        reached_real_host = False
        for url in (input_url, *collected.redirect_chain):
            host = _http_host(url)
            if not reached_real_host and is_shortener(host):
                continue
            reached_real_host = True
            if not host or check_link_official(brand, judged_domain, host) != "official":
                official = "unresolved"
                demotion_reasons.append("REDIRECT_HOST_INVALID")
                break

    _log_official_check_diagnostics(
        initial_official=initial_official,
        final_official=official,
        initial_check_failure=initial_check_failure,
        demotion_reasons=demotion_reasons,
        browser=browser,
        collected=collected,
        final_url=final_url,
    )

    return UrlAnalysis(
        final_url=final_url,
        domain=judged_domain or _http_host(input_url),
        official=official,
    )
