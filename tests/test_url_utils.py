
import pytest

from backend.src.server.url_utils import split_message


@pytest.mark.parametrize(
    ("text", "expected_links", "expected_message"),
    [
        (
            "https://example.com/login",
            ["https://example.com/login"],
            "",
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
