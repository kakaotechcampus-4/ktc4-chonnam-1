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


class TestCheckOfficialDomainWithInjectedWhitelist:
    """운영 화이트리스트(services/official_domains.py) 데이터와 분리해서,
    판정 로직 자체가 DomainMatch 네 상태를 안정적으로 반환하는지만 본다.

    실제 택배사 허용 host 범위가 나중에 늘거나 줄어도 이 테스트들은
    영향을 받지 않는다 — 데이터는 OFFICIAL_DOMAINS에서, 로직 검증은
    여기 주입한 작은 화이트리스트로 분리했다.
    """

    WHITELIST = {
        "테스트택배": {"test-parcel.example"},
    }

    def test_exact_host_match_is_official(self):
        assert (
            check_official_domain(
                "테스트택배", "test-parcel.example", whitelist=self.WHITELIST
            )
            == "official"
        )

    def test_subdomain_of_registered_host_is_official(self):
        assert (
            check_official_domain(
                "테스트택배", "tracking.test-parcel.example", whitelist=self.WHITELIST
            )
            == "official"
        )

    def test_unregistered_brand_is_not_registered(self):
        assert (
            check_official_domain(
                "등록안된브랜드", "example.com", whitelist=self.WHITELIST
            )
            == "not_registered"
        )

    def test_empty_whitelist_makes_every_brand_not_registered(self):
        assert (
            check_official_domain("테스트택배", "test-parcel.example", whitelist={})
            == "not_registered"
        )

    def test_registered_brand_wrong_host_is_brand_mismatch(self):
        assert (
            check_official_domain(
                "테스트택배", "test-parcel-fake.example", whitelist=self.WHITELIST
            )
            == "brand_mismatch"
        )

    def test_domain_unresolvable_is_unresolved_regardless_of_whitelist(self):
        assert (
            check_official_domain("테스트택배", None, whitelist=self.WHITELIST)
            == "unresolved"
        )
        assert (
            check_official_domain("테스트택배", "   ", whitelist=self.WHITELIST)
            == "unresolved"
        )

    def test_default_whitelist_is_production_data_when_not_injected(self):
        """whitelist 인자를 생략하면 실제 운영 데이터(OFFICIAL_DOMAINS)를 쓴다."""
        assert check_official_domain("CJ대한통운", "cjlogistics.com") == "official"


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


class TestBrandForDomain:
    """#164: 문자에 기관 이름이 없어도 공식 주소 목록의 주소면 그 기관으로 대조한다."""

    def test_official_carrier_domain_gives_brand(self):
        from services.official_domain_service import brand_for_domain
        assert brand_for_domain("www.hanjin.com") == "한진택배"
        assert brand_for_domain("hanjin.co.kr") == "한진택배"
        assert brand_for_domain("service.epost.go.kr") == "우체국택배"

    def test_unlisted_or_lookalike_domain_gives_none(self):
        from services.official_domain_service import brand_for_domain
        for domain in ("hanjin-delivery.com", "hanjin.com.evil.top", "evil.top", "", None):
            assert brand_for_domain(domain) is None

    def test_user_content_host_is_not_used(self):
        # 블로그처럼 누구나 글을 올리는 주소는 주소만으로 기관을 정하지 않는다
        from services.official_domain_service import brand_for_domain
        assert brand_for_domain("blog.naver.com") is None


class TestScopeExpansion161:
    """#161: 택배 문자를 많이 보내는 쇼핑몰(쿠팡)과 소유가 분명한 대학 도메인을 대조한다 (ADR 0011)."""

    def test_coupang_message_brand_matches_coupang_domain(self):
        assert check_official_domain("쿠팡", "www.coupang.com") == "official"
        assert check_official_domain("쿠팡", "coupang-event.top") == "brand_mismatch"

    def test_university_domain_found_without_brand(self):
        from services.official_domain_service import brand_for_domain
        assert brand_for_domain("international.jnu.ac.kr") == "전남대학교"
        assert brand_for_domain("jnu.ac.kr.evil.top") is None

    def test_coupang_short_link_is_resolved_not_trusted(self):
        # coupa.ng 는 쿠팡 소유라는 공식 근거가 없어 공식 목록에 넣지 않는다. 단축 주소로 보고 도착 주소로 대조한다
        from services.link_resolution import is_shortener
        from services.official_domain_service import brand_for_domain
        assert is_shortener("coupa.ng")
        assert brand_for_domain("coupa.ng") is None
