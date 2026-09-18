import pytest

from ai.kb.normalize import normalize


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("우체국택배 확인부탁합니다", "우체국택배확인부탁합니다"),
        ("우-체-국-택-배 확-인-부-탁-합-니-다", "우체국택배확인부탁합니다"),
        ("우체&국택배 배송&했습니다.", "우체국택배배송했습니다"),
        ("우체국택 배배송했습니다~", "우체국택배배송했습니다"),
        ("[Web발신]한진택배 확인부탁합니다", "한진택배확인부탁합니다"),
        ("[국외발신] 소포 배달 시도에 실패했습니다.", "소포배달시도에실패했습니다"),
        ("C·J대한통운 택배가 도착했습니다", "cj대한통운택배가도착했습니다"),
        ("CJ대한통운 택배가 도착했습니다", "cj대한통운택배가도착했습니다"),
        ("Camp;J대한통운 택배가 도착해습니다", "cj대한통운택배가도착해습니다"),
        ("&nbsp; (우 체 국 택 배 )&nbsp; 배송 했 습 니 다", "우체국택배배송했습니다"),
        ("[Web발신] 배송불가&l;도로명불일치&g;앱 다운로드", "배송불가도로명불일치앱다운로드"),
        ("택.배.도.착.햇.습.니.다", "택배도착햇습니다"),
        ("송장번호 [568******77]미확인입니다", "송장번호56877미확인입니다"),
    ],
)
def test_normalize_strips_obfuscation(raw, expected):
    assert normalize(raw) == expected


def test_normalize_handles_empty_string():
    assert normalize("") == ""


def test_normalize_does_not_merge_lg_from_entity_leftovers():
    # &l; &g; 를 먼저 제거하지 않으면 "lg"가 남아 LG로 오인됩니다.
    assert "lg" not in normalize("배송불가&l;도로명&g;")
