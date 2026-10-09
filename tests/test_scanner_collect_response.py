"""scanner.collect_response.parse_collect_response 테스트.

실제 EC2 격리환경 없이, /collect가 돌려줄 법한 dict(이미 json.loads()
된 상태)를 흉내 내서 검증한다. 정상 응답, 잘못된 JSON(비-dict), 필드
누락, HTML 크기 초과, failures 처리를 모두 다룬다 (윤여경님 요청).

윤여경님 리뷰 반영(2026-10-09):
- 멀티바이트 문자 경계에서 자를 때 errors="replace"가 대체 문자(3바이트)를
  끼워 넣어 128 KiB를 다시 넘길 수 있던 문제 — errors="ignore"로 수정.
- 응답의 input_url이 실제 요청 URL과 같은지 검증이 없던 문제 — 호출부가
  요청한 URL(expected_url)을 넘기고, 응답 값과 다르면 신뢰하지 않도록 수정.
"""

from scanner.collect_response import _truncate_html, parse_collect_response
from scanner.fetch import HTML_LIMIT_BYTES

URL = "https://link24.kr/x"


def _full_response(**overrides) -> dict:
    base = {
        "input_url": URL,
        "final_url": "https://getbarrel.com/landing",
        "redirect_chain": ["https://getbarrel.com/landing"],
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "html": "<html><title>landing</title></html>",
        "title": "landing",
        "elapsed_ms": 842,
        "failures": [],
    }
    base.update(overrides)
    return base


def _parse(data, url: str = URL):
    return parse_collect_response(url, data)


class TestNormalResponse:
    def test_all_fields_round_trip(self):
        result = _parse(_full_response())

        assert result.input_url == URL
        assert result.final_url == "https://getbarrel.com/landing"
        assert result.redirect_chain == ("https://getbarrel.com/landing",)
        assert result.status_code == 200
        assert result.content_type == "text/html; charset=utf-8"
        assert result.html == "<html><title>landing</title></html>"
        assert result.title == "landing"
        assert result.elapsed_ms == 842
        assert result.failures == ()

    def test_empty_redirect_chain_is_empty_tuple(self):
        result = _parse(_full_response(redirect_chain=[]))

        assert result.redirect_chain == ()


class TestInvalidJson:
    """호출부(isolation_client.py)가 JSON 디코딩에 실패하면 None 등
    dict가 아닌 값을 넘길 수 있다 — 예외 없이 안전하게 처리한다."""

    def test_none_is_treated_as_invalid_response(self):
        result = _parse(None)

        assert result.failures == ("invalid_response",)
        assert result.input_url == URL  # 우리가 요청한 값은 그대로 신뢰
        assert result.html == ""
        assert result.final_url is None
        assert result.redirect_chain == ()

    def test_non_dict_types_do_not_raise(self):
        for bad in ("not a dict", 42, [1, 2, 3], object()):
            result = _parse(bad)
            assert result.failures == ("invalid_response",)
            assert result.input_url == URL


class TestUrlValidation:
    """멘토(=리더) 리뷰(2026-10-09): 응답의 input_url이 실제 요청 URL과
    같은지 검증이 빠져 있었다."""

    def test_matching_input_url_passes_through(self):
        result = _parse(_full_response(input_url=URL))

        assert result.failures == ()
        assert result.input_url == URL

    def test_mismatched_input_url_is_rejected(self):
        result = _parse(_full_response(input_url="https://different.example/"))

        assert result.failures == ("url_mismatch",)
        assert result.input_url == URL  # 응답 값이 아니라 우리가 요청한 값
        assert result.html == ""
        assert result.final_url is None

    def test_missing_input_url_is_invalid_response(self):
        data = _full_response()
        del data["input_url"]

        result = _parse(data)

        assert result.input_url == URL
        assert result.failures == ("invalid_response",)

    def test_blank_input_url_is_mismatch_not_invalid(self):
        """빈 문자열은 문자열이긴 하니 "필드 없음"이 아니라 "요청과 다름"이다."""

        result = _parse(_full_response(input_url=""))

        assert result.failures == ("url_mismatch",)
        assert result.input_url == URL


class TestMissingFields:
    def test_missing_optional_fields_default_safely_without_marking_invalid(self):
        data = _full_response()
        for key in ("final_url", "status_code", "content_type", "title", "elapsed_ms"):
            del data[key]

        result = _parse(data)

        assert result.final_url is None
        assert result.status_code is None
        assert result.content_type is None
        assert result.title is None
        assert result.elapsed_ms == 0
        assert result.failures == ()

    def test_wrong_types_are_coerced_to_safe_defaults(self):
        result = _parse(_full_response(
            final_url=123,
            redirect_chain="not-a-list",
            status_code="200",
            elapsed_ms="842",
            title=["not", "a", "string"],
        ))

        assert result.final_url is None
        assert result.redirect_chain == ()
        assert result.status_code is None
        assert result.elapsed_ms == 0
        assert result.title is None

    def test_bool_is_not_mistaken_for_int(self):
        """bool은 int의 서브클래스라 isinstance(True, int)가 참이 된다 —
        status_code/elapsed_ms에 bool이 오면 숫자로 취급하지 않는다."""

        result = _parse(_full_response(status_code=True, elapsed_ms=False))

        assert result.status_code is None
        assert result.elapsed_ms == 0

    def test_redirect_chain_drops_non_string_items(self):
        result = _parse(_full_response(
            redirect_chain=["https://a.example/", 123, None, "https://b.example/"]
        ))

        assert result.redirect_chain == ("https://a.example/", "https://b.example/")


class TestHtmlOverLimit:
    def test_html_over_128kib_is_truncated_and_noted(self):
        oversized = "a" * (HTML_LIMIT_BYTES + 10)

        result = _parse(_full_response(html=oversized))

        assert len(result.html.encode("utf-8")) <= HTML_LIMIT_BYTES
        assert "html_truncated" in result.failures

    def test_html_within_limit_is_not_truncated(self):
        exact = "a" * HTML_LIMIT_BYTES

        result = _parse(_full_response(html=exact))

        assert result.html == exact
        assert "html_truncated" not in result.failures

    def test_missing_html_defaults_to_empty_string(self):
        data = _full_response()
        del data["html"]

        result = _parse(data)

        assert result.html == ""
        assert "html_truncated" not in result.failures

    def test_multibyte_boundary_does_not_overshoot_the_byte_limit(self):
        """윤여경님 리뷰: 한글처럼 여러 바이트 문자가 경계에서 잘리면
        errors="replace"의 대체 문자(3바이트)가 끼어들어 128 KiB를 다시
        넘길 수 있었다. 3바이트 한글 문자로 경계를 정확히 맞춰 재현한다."""

        # 한 글자가 3바이트라 한 바이트만 모자라게 자르도록 길이를 구성한다.
        count = HTML_LIMIT_BYTES // 3 + 1
        oversized = "안" * count

        result = _parse(_full_response(html=oversized))

        assert len(result.html.encode("utf-8")) <= HTML_LIMIT_BYTES
        assert "html_truncated" in result.failures
        # 대체 문자(U+FFFD)가 섞여 들어가지 않아야 한다.
        assert "�" not in result.html

    def test_truncation_never_overshoots_across_all_cut_offsets(self):
        """3바이트 문자 경계의 세 가지 자투리 위치(0/1/2바이트 남음)를
        전부 확인한다."""

        text = "안" * 200

        for offset in (0, 1, 2):
            limit = 300 + offset
            truncated, was_truncated = _truncate_html(text, limit)
            assert len(truncated.encode("utf-8")) <= limit
            assert was_truncated is True


class TestFailuresHandling:
    def test_failures_from_isolation_env_are_preserved(self):
        result = _parse(_full_response(
            failures=["tls_cert_verify_failed", "blocked_address"]
        ))

        assert result.failures == ("tls_cert_verify_failed", "blocked_address")

    def test_missing_failures_defaults_to_empty_tuple(self):
        data = _full_response()
        del data["failures"]

        result = _parse(data)

        assert result.failures == ()

    def test_non_list_failures_is_ignored_not_raised(self):
        result = _parse(_full_response(failures="tls_cert_verify_failed"))

        assert result.failures == ()

    def test_failures_with_non_string_items_drops_them(self):
        result = _parse(_full_response(failures=["timeout", 42, None]))

        assert result.failures == ("timeout",)

    def test_html_truncated_combines_with_isolation_reported_failures(self):
        oversized = "a" * (HTML_LIMIT_BYTES + 10)

        result = _parse(_full_response(
            html=oversized, failures=["tls_cert_verify_failed"]
        ))

        assert result.failures == ("tls_cert_verify_failed", "html_truncated")
