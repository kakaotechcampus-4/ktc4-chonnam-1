
import pytest

from backend.src.server.url_utils import has_malformed_url, split_message


@pytest.mark.parametrize(
    ("text", "expected_links", "expected_message"),
    [
        (
            "https://example.com/login",
            ["https://example.com/login"],
            "",
        ),
        (
            "택배 확인: https://example.com/",
            ["https://example.com/"],
            "택배 확인:",
        ),
        (
            "example.com/login",
            ["https://example.com/login"],
            "",
        ),
        (
            "han.gl/AbCd",
            ["https://han.gl/AbCd"],
            "",
        ),
        (
            "택배 확인: han.gl/AbCd",
            ["https://han.gl/AbCd"],
            "택배 확인:",
        ),
        (
            "무게는 3.5kg입니다",
            [],
            "무게는 3.5kg입니다",
        ),
        (
            "[택배 확인](han.gl/AbCd)",
            ["https://han.gl/AbCd"],
            "",
        ),
        (
            "example.com example.com",
            ["https://example.com"],
            "",
        ),
    ],
)
def test_split_message(text, expected_links, expected_message):
    assert split_message(text) == (expected_links, expected_message)


@pytest.mark.parametrize("url", [
    "https://cjlogistics.com.delivery-check.example/address",
    "http://unregistered.example/path", "https://example.com/path",
    "https://bit.ly/AbCd", "https://localhost/path", "https://127.0.0.1/path",
    "https://[::1]/path", "https://169.254.169.254/latest",
])
def test_explicit_url_syntax_does_not_require_a_public_suffix(url):
    assert split_message(f"확인 {url}") == ([url], "확인")
    assert not has_malformed_url(url)


@pytest.mark.parametrize("url", [
    "https://", "https:/example.com/path", "http:example.com/path",
    "https://example.com:bad/path", "https://example.com:65536/path",
    "https://example.com:/path", "https://bad..example/path",
    "https://-bad.example/path", "https://user@example.com/path",
    "https://example.com\\@evil.example/path", "https://[invalid]/path",
])
def test_malformed_explicit_urls_are_not_repaired_into_other_links(url):
    assert split_message(url) == ([], url)
    assert has_malformed_url(url)


@pytest.mark.parametrize("text", ["일반 문자입니다", "010-1234-5678", "1588-1234", "123456", "3.5kg", "", "unknown.example/path"])
def test_plain_text_and_numbers_are_not_urls(text):
    assert split_message(text) == ([], text)
    assert not has_malformed_url(text)


@pytest.mark.parametrize("text", ["https://bit.ly/AbCd", "bit.ly/AbCd"])
def test_shortener_input_forms_still_match(text):
    assert split_message(text) == (["https://bit.ly/AbCd"], "")


def test_markdown_and_duplicate_explicit_unknown_suffix():
    url = "https://parcel.example/path"
    assert split_message(f"[조회]({url}) {url}") == ([url], "")
