"""services.official_domain_service의 판정 함수 단위 테스트.

BE_DOMAIN_MATCH_TASK_GUIDE.md 10절 "필수 테스트" 표를 그대로 반영한다.
"""

import pytest

from services.official_domain_service import (
    check_official_domain,
    is_same_or_subdomain,
)


class TestCheckOfficialDomain:
    # --- 공식 도메인 일치 ---

    def test_exact_match(self):
        assert check_official_domain("NAVER", "naver.com") == "official"

    def test_www_subdomain(self):
        assert check_official_domain("NAVER", "www.naver.com") == "official"

    def test_blog_subdomain(self):
        assert check_official_domain("NAVER", "blog.naver.com") == "official"

    def test_multi_level_subdomain(self):
        assert check_official_domain("NAVER", "n.news.naver.com") == "official"

    # --- 브랜드는 맞지만 도메인이 다름 (위장) ---

    def test_lookalike_hyphenated_domain(self):
        assert check_official_domain("NAVER", "naver-login.com") == "brand_mismatch"

    def test_lookalike_fake_prefix(self):
        assert check_official_domain("NAVER", "fake-naver.com") == "brand_mismatch"

    def test_lookalike_no_separator(self):
        assert check_official_domain("NAVER", "notnaver.com") == "brand_mismatch"

    def test_lookalike_domain_as_subdomain_of_attacker(self):
        """공식 도메인 문자열을 포함하지만 실제로는 공격자 도메인인 경우.

        단순 `official_domain in domain` 검사였다면 이 케이스를 놓친다.
        """
        assert (
            check_official_domain("NAVER", "naver.com.attacker.com")
            == "brand_mismatch"
        )

    # --- 대조 불가 ---

    def test_domain_none_is_unresolved(self):
        assert check_official_domain("NAVER", None) == "unresolved"

    def test_domain_blank_is_unresolved(self):
        assert check_official_domain("NAVER", "   ") == "unresolved"

    def test_unregistered_brand_is_not_registered(self):
        assert check_official_domain("UNKNOWN", "example.com") == "not_registered"

    def test_brand_none_is_not_registered(self):
        assert check_official_domain(None, "example.com") == "not_registered"

    # --- 입력 정규화 ---

    def test_uppercase_domain_normalized(self):
        assert check_official_domain("NAVER", "WWW.NAVER.COM") == "official"

    def test_trailing_dot_normalized(self):
        assert check_official_domain("NAVER", "www.naver.com.") == "official"

    def test_surrounding_whitespace_normalized(self):
        assert check_official_domain("NAVER", "  naver.com  ") == "official"

    # --- 실제 서비스 브랜드(택배) ---

    def test_real_brand_official(self):
        assert (
            check_official_domain("CJ대한통운", "www.cjlogistics.com")
            == "official"
        )

    def test_real_brand_multi_domain_whitelist(self):
        # 한진택배는 공식 도메인이 두 개(hanjin.com/hanjin.co.kr) 등록돼 있다.
        assert check_official_domain("한진택배", "hanjin.co.kr") == "official"
        assert check_official_domain("한진택배", "hanjin.com") == "official"

    def test_real_brand_mismatch(self):
        assert (
            check_official_domain("CJ대한통운", "cj-logistics-event.com")
            == "brand_mismatch"
        )

    def test_unverified_brand_is_not_registered(self):
        # 대신택배·KGB택배는 실제 공식 도메인을 확인하지 못해 화이트리스트에
        # 없다 — 추측으로 official/brand_mismatch를 만들지 않는다.
        assert check_official_domain("대신택배", "daesin.co.kr") == "not_registered"


class TestIsSameOrSubdomain:
    def test_exact_match(self):
        assert is_same_or_subdomain("naver.com", "naver.com") is True

    def test_subdomain(self):
        assert is_same_or_subdomain("blog.naver.com", "naver.com") is True

    def test_not_a_subdomain_lookalike(self):
        assert is_same_or_subdomain("notnaver.com", "naver.com") is False

    def test_official_domain_as_substring_of_attacker_domain(self):
        assert is_same_or_subdomain("naver.com.attacker.com", "naver.com") is False

    def test_case_and_whitespace_insensitive(self):
        assert is_same_or_subdomain(" WWW.NAVER.COM. ", "naver.com") is True


@pytest.mark.parametrize(
    ("brand", "domain", "expected"),
    [
        ("NAVER", "naver.com", "official"),
        ("NAVER", "www.naver.com", "official"),
        ("NAVER", "blog.naver.com", "official"),
        ("NAVER", "n.news.naver.com", "official"),
        ("NAVER", "naver-login.com", "brand_mismatch"),
        ("NAVER", "fake-naver.com", "brand_mismatch"),
        ("NAVER", "notnaver.com", "brand_mismatch"),
        ("NAVER", "naver.com.attacker.com", "brand_mismatch"),
        ("NAVER", None, "unresolved"),
        ("UNKNOWN", "example.com", "not_registered"),
        ("NAVER", "WWW.NAVER.COM", "official"),
        ("NAVER", "www.naver.com.", "official"),
    ],
)
def test_guide_required_cases(brand, domain, expected):
    """BE_DOMAIN_MATCH_TASK_GUIDE.md 10절 표를 그대로 파라미터화한 버전."""
    assert check_official_domain(brand, domain) == expected
