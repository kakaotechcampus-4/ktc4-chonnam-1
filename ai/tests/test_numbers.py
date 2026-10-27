"""기관 대표번호 KB 와 번호 대조 (v3 §6.3·§6.5)."""

import pytest
import yaml

from ai.case.types import Conclusion, NumberCheck, Scenario, VoiceCaseResult
from ai.kb import numbers
from ai.kb.numbers import (
    NUMBERS_PATH,
    check_phone_number,
    find_numbers,
    normalize_number,
    resolve_org,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("02-1234-5678", "0212345678"),
        ("(02)1234-5678", "0212345678"),
        ("(02) 1234-5678", "0212345678"),
        ("+82-2-1234-5678", "0212345678"),
        ("+82 10-1234-5678", "01012345678"),
        ("+82-(0)10-1234-5678", "01012345678"),
        ("1332", "1332"),
        ("1588-0000", "15880000"),
        ("112", "112"),
        ("010.1234.5678", "01012345678"),
    ],
)
def test_normalize_number_accepts_phone_shapes(raw, expected):
    assert normalize_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12", "1234567", "2026", "123456789012", "5000원"])
def test_normalize_number_rejects_non_phone(raw):
    assert normalize_number(raw) is None


def test_find_numbers_picks_phone_candidates():
    text = "금감원입니다. 02-1234-5678 로 전화하시고 1588-0000 도 됩니다. 010 1234 5678 문자 주세요"
    assert find_numbers(text) == ["0212345678", "15880000", "01012345678"]


def test_find_numbers_skips_amounts_and_years():
    assert find_numbers("2026년 3월에 1000원, 3,000만원을 보내세요") == []


def test_find_numbers_dedupes_and_handles_short_numbers():
    assert find_numbers("112에 신고하세요. 112 로 전화") == ["112"]
    assert find_numbers("1332번으로 문의") == ["1332"]


def test_resolve_org_by_alias_and_official_name():
    assert resolve_org("금감원") == "금융감독원"
    assert resolve_org("저는 금융감독원 수사관입니다") == "금융감독원"
    assert resolve_org("서울중앙지검 검사입니다") == "대검찰청"
    assert resolve_org("국민은행") == "KB국민은행"
    assert resolve_org("농협카드") == "NH농협카드"
    assert resolve_org("아무개 회사") is None


def test_resolve_org_skips_draft_entries():
    # 경찰청은 draft 라 aliases 로도 찾지 않는다
    assert resolve_org("경찰청") is None


def test_check_official_number():
    check = check_phone_number("1332", "금감원")
    assert check.result == "official"
    assert check.org == "금융감독원"
    assert check.source_url.startswith("https://www.fss.or.kr")


def test_check_accepts_formatted_number():
    assert check_phone_number("02-3145-5114", "금융감독원").result == "official"


def test_check_not_official():
    check = check_phone_number("02-1234-5678", "금감원")
    assert check.result == "not_official"
    assert check.source_url is not None


def test_check_org_not_listed():
    check = check_phone_number("1332", "듣도보도못한기관")
    assert check.result == "org_not_listed"
    assert check.source_url is None


def test_draft_entries_are_not_used():
    # 경찰청 112 는 draft 라 대조에 쓰지 않는다
    assert check_phone_number("112", "경찰청").result == "org_not_listed"


def test_unparseable_number_is_not_official():
    assert check_phone_number("abc", "금감원").result == "not_official"


def test_draft_not_loaded_from_custom_path(tmp_path):
    path = tmp_path / "n.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "orgs": [
                    {"org": "가기관", "aliases": ["가기관"], "numbers": ["1111"],
                     "source_url": "https://a.example", "status": "curated"},
                    {"org": "나기관", "aliases": ["나기관"], "numbers": ["2222"],
                     "source_url": "https://b.example", "status": "draft"},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert check_phone_number("1111", "가기관", path=path).result == "official"
    assert check_phone_number("2222", "나기관", path=path).result == "org_not_listed"


def test_yaml_schema_every_entry():
    data = yaml.safe_load(NUMBERS_PATH.read_text(encoding="utf-8"))
    seen = set()
    for entry in data["orgs"]:
        assert entry["org"] not in seen
        seen.add(entry["org"])
        assert entry["status"] in {"curated", "draft"}
        assert entry["aliases"] and entry["numbers"]
        assert entry["source_url"].startswith("https://")
        assert str(entry["checked_at"]) == "2026-10-28"
        for number in entry["numbers"]:
            assert isinstance(number, str)
            assert normalize_number(number) == number


def test_curated_entries_have_source_url():
    for org in numbers.load_orgs():
        assert org.source_url.startswith("https://")


def test_voice_case_result_number_checks_default_empty():
    result = VoiceCaseResult(scenario=Scenario.VOICE_PHISHING, conclusion=Conclusion.INCONCLUSIVE)
    assert result.number_checks == []
    check = NumberCheck(number="1332", org="금융감독원", result="official", source_url="https://x")
    assert VoiceCaseResult(
        scenario=Scenario.VOICE_PHISHING,
        conclusion=Conclusion.SUSPECTED,
        number_checks=[check],
    ).number_checks == [check]
