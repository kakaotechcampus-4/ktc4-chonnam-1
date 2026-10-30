"""LLM 이 짚은 링크 구간의 검증과 정규형 (ADR 0007)."""

import pytest

from ai.links import defang, message_links, remove_spans, spoken_link, valid_host
from ai.types import LinkCandidate, LinkOrigin


def urls(text: str, spans: list[str]) -> list[str]:
    return [link.url for _, link in message_links(text, spans)]


@pytest.mark.parametrize("text, spans, expected", [
    ("주소 확인: https://cj-deliver.top/a?x=1 바랍니다", ["https://cj-deliver.top/a?x=1"],
     ["https://cj-deliver.top/a?x=1"]),
    ("bit.ly/abc로 접속해 주세요", ["bit.ly/abc"], ["https://bit.ly/abc"]),
    # LLM 이 조사까지 붙여 와도 주소에 넣지 않는다
    ("bit.ly/abc로 접속해 주세요", ["bit.ly/abc로"], ["https://bit.ly/abc"]),
    ("확인: bit[.]ly/abc123", ["bit[.]ly/abc123"], ["https://bit.ly/abc123"]),
    ("반송 예정. cj-deliver . top 에서 확인", ["cj-deliver . top"], ["https://cj-deliver.top/"]),
    ("hxxps://evil[.]top/login 접속", ["hxxps://evil[.]top/login"], ["https://evil.top/login"]),
    ("주소: example.com/a. 감사합니다", ["example.com/a."], ["https://example.com/a"]),
    ("확인하세요.bit.ly/x", ["bit.ly/x"], ["https://bit.ly/x"]),
    ("HTTPS://BIT.LY/AbC", ["HTTPS://BIT.LY/AbC"], ["https://bit.ly/AbC"]),
    ("http://1.2.3.4/login 접속", ["http://1.2.3.4/login"], ["http://1.2.3.4/login"]),
    ("[택배 확인](han.gl/AbCd)", ["han.gl/AbCd"], ["https://han.gl/AbCd"]),
])
def test_written_link_becomes_normal_form(text, spans, expected):
    assert urls(text, spans) == expected


@pytest.mark.parametrize("text, span", [
    # 글자를 고친 주소는 원문에 없다
    ("확인: cjlogistlcs.com/track", "cjlogistics.com/track"),
    # 더 긴 주소에서 공식처럼 보이는 부분만 떼어 낸 구간
    ("확인: cjlogistics.com.evil.top/a", "cjlogistics.com"),
    ("확인: evil-cjlogistics.com", "cjlogistics.com"),
    ("www.cjlogistics.com。evil.top 접속", "www.cjlogistics.com"),
    ("https://www.cjlogistics.com@evil.top/a", "https://www.cjlogistics.com"),
    ("www.cjlogistics.com 확인", "cjlogistics.com"),
    # 링크가 아닌 것
    ("문의: help@cjlogistics.com", "help@cjlogistics.com"),
    ("무게는 3.5kg 입니다", "3.5"),
    ("버전 1.2.3.4 업데이트", "1.2.3.4"),
    ("ftp://files.example.com/a", "ftp://files.example.com/a"),
    ("그냥 문장입니다", "그냥 문장"),
])
def test_unverified_span_is_dropped(text, span):
    assert message_links(text, [span]) == []


def test_userinfo_link_is_judged_by_real_host():
    text = "https://www.cjlogistics.com@evil.top/a"
    [(_, link)] = message_links(text, [text])
    assert link.domain == "evil.top"


def test_short_link_inside_longer_listed_link_is_not_cut():
    # 공식 주소 옆의 비슷한 짧은 주소를 잘린 구간으로 버리면 공식 링크만 남는다
    text = "조회 cjlogistics.com 수정 cjlogistics.co"
    assert urls(text, ["cjlogistics.com", "cjlogistics.co"]) == [
        "https://cjlogistics.com/", "https://cjlogistics.co/"]


def test_span_only_inside_longer_link_is_not_a_link():
    assert urls("확인: bit.ly/abc", ["bit.ly", "bit.ly/abc"]) == ["https://bit.ly/abc"]


def test_every_occurrence_must_be_whole():
    text = "cjlogistics.com 확인 후 cjlogistics.com.evil.top 접속"
    assert message_links(text, ["cjlogistics.com"]) == []


def test_links_follow_text_order():
    assert urls("먼저 a.kr 다음 b.kr", ["b.kr", "a.kr"]) == ["https://a.kr/", "https://b.kr/"]


def test_link_keeps_span_and_message_origin():
    [(span, link)] = message_links("확인 bit.ly/x", ["bit.ly/x"])
    assert span == "bit.ly/x"
    assert link == LinkCandidate(url="https://bit.ly/x", domain="bit.ly", origin=LinkOrigin.MESSAGE)


def test_remove_spans_matches_split_message_shape():
    assert remove_spans("택배 확인: han.gl/AbCd", ["han.gl/AbCd"]) == "택배 확인:"
    # 긴 구간부터 지워 짧은 구간이 긴 주소를 쪼개지 않는다
    assert remove_spans("a bit.ly/abc b bit.ly", ["bit.ly", "bit.ly/abc"]) == "a  b"


@pytest.mark.parametrize("text, expected", [
    ("bit[.]ly", "bit.ly"),
    ("bit(.)ly", "bit.ly"),
    ("bit[dot]ly", "bit.ly"),
    ("hXXps://a", "https://a"),
    ("a。kr", "a.kr"),
    ("cj-deliver . top", "cj-deliver.top"),
    # 문장 끝의 점은 붙이지 않는다
    ("cj.com. Then", "cj.com. Then"),
])
def test_defang(text, expected):
    assert defang(text) == expected


@pytest.mark.parametrize("host, ok", [
    ("cjlogistics.com", True),
    ("a.kr", True),
    ("xn--3e0b707e.kr", True),
    ("1.2.3.4", True),
    ("3.5", False),
    ("010.1234.5678", False),
    ("localhost", False),
    ("-a.com", False),
    ("a_b.com", False),
    ("한국.kr", False),
])
def test_valid_host(host, ok):
    assert valid_host(host) is ok


def test_spoken_link_has_spoken_origin():
    assert spoken_link("Spo-Case.com") == LinkCandidate(
        url="https://spo-case.com/", domain="spo-case.com", origin=LinkOrigin.SPOKEN)
    assert spoken_link("010.1234.5678") is None
