"""scanner.static_checks 테스트.

docs/scanner-static-checks-proposal.md 기준. 접속 없이 도메인 문자열만
보고 하는 1단계 검사 — 기존 공식 도메인 비교 + KISA 목록 대조 +
Punycode/Unicode 사칭 분석.
"""

import idna

from scanner.static_checks import (
    check_static,
    decode_domain,
    find_lookalike,
    is_kisa_listed,
    skeleton,
)

WHITELIST = {
    "CJ대한통운": {"cjlogistics.com"},
    "한진택배": {"hanjin.com", "hanjin.co.kr"},
}


def _cyrillic_lookalike_of(domain: str) -> str:
    """domain의 등록 도메인 부분(a/c/e/o/p 등)만 Cyrillic 동형이의 문자로
    바꾼 뒤 punycode로 인코딩한다. TLD(.com 등)는 실제 공격처럼 그대로
    둔다 — TLD까지 바꾸면 실제로는 다른(존재할 수 없는) TLD가 되어 버려
    현실적인 사칭 시나리오가 아니다."""

    name, _, tld = domain.rpartition(".")
    swap = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р"}
    spoofed_name = "".join(swap.get(ch, ch) for ch in name)
    assert spoofed_name != name, "최소 한 글자는 바뀌어야 테스트 의미가 있다"
    return idna.encode(f"{spoofed_name}.{tld}").decode("ascii")


class TestDecodeDomain:
    def test_plain_ascii_domain_is_not_punycode(self):
        decoded, is_punycode = decode_domain("cjlogistics.com")

        assert (decoded, is_punycode) == (None, False)

    def test_punycode_domain_decodes_to_unicode(self):
        decoded, is_punycode = decode_domain("xn--bj0bj06e.com")  # 한글.com

        assert is_punycode is True
        assert decoded == "한글.com"

    def test_punycode_subdomain_decodes(self):
        puny = _cyrillic_lookalike_of("cjlogistics.com")

        decoded, is_punycode = decode_domain(f"sub.{puny}")

        assert is_punycode is True
        assert decoded is not None and decoded.endswith(".com")

    def test_malformed_punycode_label_fails_without_raising(self):
        decoded, is_punycode = decode_domain("xn--invalid-!!.com")

        assert is_punycode is True
        assert decoded is None


class TestSkeleton:
    def test_cyrillic_lookalike_has_same_skeleton_as_latin(self):
        assert skeleton("сjlogistics.com") == skeleton("cjlogistics.com")

    def test_different_domains_have_different_skeletons(self):
        assert skeleton("hanjin.com") != skeleton("cjlogistics.com")

    def test_case_and_trailing_dot_are_normalized(self):
        assert skeleton("CJLOGISTICS.COM.") == skeleton("cjlogistics.com")


class TestFindLookalike:
    def test_exact_official_domain_is_not_a_lookalike_of_itself(self):
        assert find_lookalike("cjlogistics.com", None, whitelist=WHITELIST) is None

    def test_subdomain_of_official_is_not_a_lookalike(self):
        assert find_lookalike("track.cjlogistics.com", None, whitelist=WHITELIST) is None

    def test_cyrillic_spoofed_domain_is_found_across_whole_whitelist(self):
        puny = _cyrillic_lookalike_of("hanjin.com")
        decoded = idna.decode(puny)

        assert find_lookalike(puny, decoded, whitelist=WHITELIST) == "hanjin.com"

    def test_unrelated_domain_is_not_a_lookalike(self):
        assert find_lookalike("naver.com", None, whitelist=WHITELIST) is None


class TestIsKisaListed:
    def test_listed_raw_domain(self):
        assert is_kisa_listed("evil.example", None, frozenset({"evil.example"})) is True

    def test_listed_only_after_decoding(self):
        assert is_kisa_listed(
            "xn--bj0bj06e.com", "한글.com", frozenset({"한글.com"})
        ) is True

    def test_not_listed(self):
        assert is_kisa_listed("safe.example", None, frozenset({"evil.example"})) is False

    def test_case_and_trailing_dot_are_normalized(self):
        assert is_kisa_listed("EVIL.EXAMPLE.", None, frozenset({"evil.example"})) is True


class TestCheckStatic:
    def test_official_domain_is_clean(self):
        result = check_static(
            "cjlogistics.com", "CJ대한통운", whitelist=WHITELIST, kisa_domains=frozenset(),
        )

        assert result.official_match == "official"
        assert (result.is_punycode, result.kisa_listed, result.lookalike_of) == (False, False, None)
        assert result.failures == ()

    def test_punycode_itself_is_not_flagged_as_risky(self):
        """정상 국제화 도메인은 허용한다 — Punycode 사용 자체는 위험 신호가 아니다."""

        result = check_static(
            "xn--bj0bj06e.com", None, whitelist=WHITELIST, kisa_domains=frozenset(),
        )

        assert result.is_punycode is True
        assert (result.kisa_listed, result.lookalike_of) == (False, None)

    def test_cyrillic_homograph_is_flagged_as_lookalike(self):
        puny = _cyrillic_lookalike_of("cjlogistics.com")

        result = check_static(puny, None, whitelist=WHITELIST, kisa_domains=frozenset())

        assert result.is_punycode is True
        assert result.lookalike_of == "cjlogistics.com"
        assert result.official_match != "official"

    def test_kisa_listed_domain_is_flagged(self):
        result = check_static(
            "evil.example", None, whitelist=WHITELIST,
            kisa_domains=frozenset({"evil.example"}),
        )

        assert result.kisa_listed is True

    def test_missing_kisa_feed_is_recorded_as_a_failure_not_a_clean_result(self):
        """KISA 데이터가 아직 연결 전이면 "확인했는데 없음"과 구분해야 한다."""

        result = check_static(
            "cjlogistics.com", "CJ대한통운", whitelist=WHITELIST, kisa_domains=None,
        )

        assert result.kisa_listed is False
        assert result.failures == ("kisa_feed_unavailable",)

    def test_blank_domain_is_unresolved_without_crashing(self):
        result = check_static(None, "CJ대한통운", whitelist=WHITELIST, kisa_domains=frozenset())

        assert result.official_match == "unresolved"
        assert result.domain == ""
