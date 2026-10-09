"""scanner.collect_response.parse_collect_response 테스트.

실제 EC2 격리환경 없이, /collect가 돌려줄 법한 dict(이미 json.loads()
된 상태)를 흉내 내서 검증한다. 정상 응답, 잘못된 JSON(비-dict), 필드
누락, HTML 크기 초과, failures 처리를 모두 다룬다 (윤여경님 요청).
"""

from scanner.collect_response import parse_collect_response
from scanner.fetch import HTML_LIMIT_BYTES


def _full_response(**overrides) -> dict:
    base = {
        "input_url": "https://link24.kr/x",
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


class TestNormalResponse:
    def test_all_fields_round_trip(self):
        result = parse_collect_response(_full_response())

        assert result.input_url == "https://link24.kr/x"
        assert result.final_url == "https://getbarrel.com/landing"
        assert result.redirect_chain == ("https://getbarrel.com/landing",)
        assert result.status_code == 200
        assert result.content_type == "text/html; charset=utf-8"
        assert result.html == "<html><title>landing</title></html>"
        assert result.title == "landing"
        assert result.elapsed_ms == 842
        assert result.failures == ()

    def test_empty_redirect_chain_is_empty_tuple(self):
        result = parse_collect_response(_full_response(redirect_chain=[]))

        assert result.redirect_chain == ()


class TestInvalidJson:
    """호출부(isolation_client.py)가 JSON 디코딩에 실패하면 None 등
    dict가 아닌 값을 넘길 수 있다 — 예외 없이 안전하게 처리한다."""

    def test_none_is_treated_as_invalid_response(self):
        result = parse_collect_response(None)

        assert result.failures == ("invalid_response",)
        assert result.input_url == ""
        assert result.html == ""
        assert result.final_url is None
        assert result.redirect_chain == ()

    def test_non_dict_types_do_not_raise(self):
        for bad in ("not a dict", 42, [1, 2, 3], object()):
            result = parse_collect_response(bad)
            assert result.failures == ("invalid_response",)


class TestMissingFields:
    def test_missing_input_url_is_invalid_response(self):
        data = _full_response()
        del data["input_url"]

        result = parse_collect_response(data)

        assert result.input_url == ""
        assert "invalid_response" in result.failures

    def test_blank_input_url_is_invalid_response(self):
        result = parse_collect_response(_full_response(input_url=""))

        assert "invalid_response" in result.failures

    def test_missing_optional_fields_default_safely_without_marking_invalid(self):
        data = _full_response()
        for key in ("final_url", "status_code", "content_type", "title", "elapsed_ms"):
            del data[key]

        result = parse_collect_response(data)

        assert result.final_url is None
        assert result.status_code is None
        assert result.content_type is None
        assert result.title is None
        assert result.elapsed_ms == 0
        assert "invalid_response" not in result.failures

    def test_wrong_types_are_coerced_to_safe_defaults(self):
        result = parse_collect_response(_full_response(
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

        result = parse_collect_response(_full_response(status_code=True, elapsed_ms=False))

        assert result.status_code is None
        assert result.elapsed_ms == 0

    def test_redirect_chain_drops_non_string_items(self):
        result = parse_collect_response(_full_response(
            redirect_chain=["https://a.example/", 123, None, "https://b.example/"]
        ))

        assert result.redirect_chain == ("https://a.example/", "https://b.example/")


class TestHtmlOverLimit:
    def test_html_over_128kib_is_truncated_and_noted(self):
        oversized = "a" * (HTML_LIMIT_BYTES + 10)

        result = parse_collect_response(_full_response(html=oversized))

        assert len(result.html.encode("utf-8")) <= HTML_LIMIT_BYTES
        assert "html_truncated" in result.failures

    def test_html_within_limit_is_not_truncated(self):
        exact = "a" * HTML_LIMIT_BYTES

        result = parse_collect_response(_full_response(html=exact))

        assert result.html == exact
        assert "html_truncated" not in result.failures

    def test_missing_html_defaults_to_empty_string(self):
        data = _full_response()
        del data["html"]

        result = parse_collect_response(data)

        assert result.html == ""
        assert "html_truncated" not in result.failures


class TestFailuresHandling:
    def test_failures_from_isolation_env_are_preserved(self):
        result = parse_collect_response(_full_response(
            failures=["tls_cert_verify_failed", "blocked_address"]
        ))

        assert result.failures == ("tls_cert_verify_failed", "blocked_address")

    def test_missing_failures_defaults_to_empty_tuple(self):
        data = _full_response()
        del data["failures"]

        result = parse_collect_response(data)

        assert result.failures == ()

    def test_non_list_failures_is_ignored_not_raised(self):
        result = parse_collect_response(_full_response(failures="tls_cert_verify_failed"))

        assert result.failures == ()

    def test_failures_with_non_string_items_drops_them(self):
        result = parse_collect_response(_full_response(failures=["timeout", 42, None]))

        assert result.failures == ("timeout",)

    def test_invalid_response_is_not_duplicated_when_already_present(self):
        data = _full_response(input_url="", failures=["invalid_response"])

        result = parse_collect_response(data)

        assert result.failures.count("invalid_response") == 1

    def test_html_truncated_combines_with_isolation_reported_failures(self):
        oversized = "a" * (HTML_LIMIT_BYTES + 10)

        result = parse_collect_response(_full_response(
            html=oversized, failures=["tls_cert_verify_failed"]
        ))

        assert result.failures == ("tls_cert_verify_failed", "html_truncated")
