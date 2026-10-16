"""scanner.isolation_page.to_isolated_page 테스트 (이슈 #60).

GitHub 이슈 #60 코멘트 2-5절의 결정표를 그대로 검증한다: HTML 있음/
없음, 실패 사유 있음/없음, 3단계(browser) 결과 우선순위.
"""

from datetime import datetime, timezone

from ai.types import FailureCode

from scanner.collect_response import parse_collect_response
from scanner.isolation_page import to_isolated_page
from scanner.models import BrowserResult, CollectResult

URL = "https://example.test/"


def _collect_result(**overrides) -> CollectResult:
    base = dict(
        input_url=URL,
        final_url=URL,
        redirect_chain=(),
        status_code=200,
        content_type="text/html; charset=utf-8",
        html="<html><body>ok</body></html>",
        title="ok",
        elapsed_ms=120,
        failures=(),
        browser=None,
    )
    base.update(overrides)
    return CollectResult(**base)


def _browser_result(**overrides) -> BrowserResult:
    base = dict(
        trigger="script_only_page",
        final_url=URL,
        navigation_chain=(),
        html="<html><body>rendered</body></html>",
        title="rendered",
        downloads=(),
        blocked_requests=0,
        elapsed_ms=900,
        failures=(),
    )
    base.update(overrides)
    return BrowserResult(**base)


class TestNoHtmlIsCollectionFailed:
    def test_empty_html_and_no_browser_is_collection_failed(self):
        result = _collect_result(html="")

        page, failure = to_isolated_page(result)

        assert page is None
        assert failure is FailureCode.COLLECTION_FAILED

    def test_browser_present_but_both_html_fields_empty(self):
        result = _collect_result(html="", browser=_browser_result(html=""))

        page, failure = to_isolated_page(result)

        assert page is None
        assert failure is FailureCode.COLLECTION_FAILED


class TestHtmlPresentWithoutFailures:
    def test_clean_result_produces_a_page_with_no_failure(self):
        result = _collect_result()

        page, failure = to_isolated_page(result)

        assert page is not None
        assert page.info == "<html><body>ok</body></html>"
        assert failure is None

    def test_collected_at_is_set_to_a_real_timestamp(self):
        before = datetime.now(timezone.utc)

        page, _ = to_isolated_page(_collect_result())

        after = datetime.now(timezone.utc)
        assert page.collected_at is not None
        assert before <= page.collected_at <= after

    def test_unobserved_brand_and_category_are_null(self):
        page, _ = to_isolated_page(_collect_result())

        assert page.brand is None
        assert page.category is None


class TestHtmlPresentWithFailures:
    def test_html_truncated_is_partial_content_but_page_is_kept(self):
        result = _collect_result(failures=("html_truncated",))

        page, failure = to_isolated_page(result)

        assert page is not None
        assert page.info == result.html
        assert failure is FailureCode.PARTIAL_CONTENT

    def test_tls_cert_verify_failed_with_html_is_still_partial_content(self):
        """verify=False 재시도로 받은 HTML도 내용은 그대로 쓴다 — 인증서
        검증 실패 자체는 "내용을 못 봤다"는 뜻이 아니다."""

        result = _collect_result(failures=("tls_cert_verify_failed",))

        page, failure = to_isolated_page(result)

        assert page is not None
        assert failure is FailureCode.PARTIAL_CONTENT


class TestBrowserResultPriority:
    def test_browser_html_is_preferred_over_stage_two_html(self):
        result = _collect_result(
            html="<html>stage2</html>",
            browser=_browser_result(html="<html>stage3-rendered</html>"),
        )

        page, failure = to_isolated_page(result)

        assert page.info == "<html>stage3-rendered</html>"
        assert failure is None

    def test_falls_back_to_stage_two_html_when_browser_html_is_empty(self):
        result = _collect_result(
            html="<html>stage2</html>",
            browser=_browser_result(html=""),
        )

        page, failure = to_isolated_page(result)

        assert page.info == "<html>stage2</html>"

    def test_browser_only_failures_still_mark_partial_content(self):
        """2단계 자체는 failures가 없어도, 3단계(browser)가 실패 사유를
        남겼으면 합쳐서 PARTIAL_CONTENT로 본다."""

        result = _collect_result(
            failures=(),
            browser=_browser_result(failures=("browser_timeout",)),
        )

        page, failure = to_isolated_page(result)

        assert page is not None
        assert failure is FailureCode.PARTIAL_CONTENT

    def test_failures_from_both_stages_are_combined(self):
        result = _collect_result(
            failures=("html_truncated",),
            browser=_browser_result(failures=("browser_timeout",)),
        )

        page, failure = to_isolated_page(result)

        assert failure is FailureCode.PARTIAL_CONTENT


class TestWithRealCollectResponseParsing:
    """parse_collect_response()가 만든 실제 CollectResult로도 동작하는지
    확인한다 (이슈 #60이 참조한 실제 /collect 응답 예시 형태)."""

    def test_realistic_collect_response_becomes_a_page(self):
        data = {
            "input_url": URL,
            "final_url": URL,
            "redirect_chain": [],
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "html": (
                "<html><head><title>CJ대한통운</title></head>"
                "<body><form action=\"/s\">"
                "<label for=\"pw\">계좌 비밀번호</label>"
                "<input id=\"pw\" type=\"password\" name=\"pw\">"
                "</form></body></html>"
            ),
            "title": "CJ대한통운",
            "elapsed_ms": 140,
            "failures": [],
            "browser": None,
        }
        result = parse_collect_response(URL, data)

        page, failure = to_isolated_page(result)

        assert page is not None
        assert "계좌 비밀번호" in page.info
        assert failure is None
