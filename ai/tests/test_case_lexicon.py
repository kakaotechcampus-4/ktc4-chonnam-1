import pytest

from ai.case.lexicon import (
    INSTITUTION_PATTERN,
    classify_demand,
    pick_other_speaker,
    read_with_lexicon,
)
from ai.case.types import (
    DemandKind,
    IdentityKind,
    InputKind,
    ReadingSource,
    Segment,
    Side,
)


def seg(speaker, text):
    return Segment(side=Side.UNKNOWN, speaker=speaker, text=text)


def source(*segments):
    return ReadingSource(kind=InputKind.AUDIO, segments=list(segments))


@pytest.mark.parametrize(
    "sentence,kind",
    [
        ("문화상품권 사서 핀번호 보내줘", DemandKind.GIFT_CARD),
        ("문자로 간 인증번호 알려줘", DemandKind.AUTH_CODE),
        ("팀뷰어 깔아 주세요", DemandKind.REMOTE_CONTROL),
        ("보안 앱을 설치하세요", DemandKind.APP_INSTALL),
        ("이름과 주민번호를 입력하시면 됩니다", DemandKind.PERSONAL_INFO),
        ("현금으로 찾아서 직원에게 전달하세요", DemandKind.CASH_WITHDRAWAL),
        ("안전계좌로 옮기셔야 합니다", DemandKind.MONEY_TRANSFER),
        ("확인은 02-1234-5678로 하세요", DemandKind.CALL_NUMBER),
        ("1234-5678 으로 전화 주세요", DemandKind.CALL_NUMBER),
        ("이 번호로 문자해", DemandKind.MOVE_MESSENGER),
        ("카톡 친구추가 해줘", DemandKind.MOVE_MESSENGER),
    ],
)
def test_demand_table_positive(sentence, kind):
    assert classify_demand(sentence) == kind


def test_demand_priority_gift_card_beats_money():
    assert classify_demand("상품권 사서 보내줘") == DemandKind.GIFT_CARD


def test_demand_priority_remote_beats_app_install():
    assert classify_demand("원격 앱 설치하세요") == DemandKind.REMOTE_CONTROL


def test_no_demand():
    assert classify_demand("안녕하세요 잘 지내세요") is None


@pytest.mark.parametrize("text", ["검사 결과가 나왔습니다", "수사 중인 계좌입니다"])
def test_institution_not_triggered(text):
    assert not INSTITUTION_PATTERN.search(text)
    reading = read_with_lexicon(source(seg("화자1", text))).reading
    assert all(i.kind != IdentityKind.INSTITUTION for i in reading.identity)


def test_institution_and_acquaintance_identity():
    r = read_with_lexicon(
        source(seg("화자1", "금융감독원 수사관입니다"), seg("화자2", "엄마 나야"))
    ).reading
    kinds = {i.kind for i in r.identity}
    assert kinds == {IdentityKind.INSTITUTION, IdentityKind.ACQUAINTANCE}


def test_ddalgi_not_acquaintance():
    r = read_with_lexicon(source(seg("화자1", "딸기 주문하셨죠"))).reading
    assert r.identity == []


def test_blockers():
    r = read_with_lexicon(source(seg("화자1", "엄마 폰이 고장 났어. 통화는 안 돼"))).reading
    assert len(r.blockers) == 2


def test_v1_script_picks_b():
    src = source(
        seg("A", "여보세요"),
        seg("B", "금융감독원 수사관 김○○입니다. 고객님 명의 계좌가 범죄에 연루됐습니다"),
        seg("A", "네? 어떻게 해야 하죠"),
        seg("B", "자산 보호를 위해 안전계좌로 옮기셔야 합니다. 확인은 02-1234-5678로 하세요"),
    )
    result = read_with_lexicon(src)
    r = result.reading
    assert result.status == "fallback"
    assert r.other_speaker == "B"
    assert r.speaker_confidence == "medium"
    assert {d.kind for d in r.demands} == {DemandKind.MONEY_TRANSFER, DemandKind.CALL_NUMBER}
    # 인용은 원문 문장 그대로
    assert "금융감독원 수사관 김○○입니다" in [i.quote for i in r.identity]
    assert "확인은 02-1234-5678로 하세요" in [d.quote for d in r.demands]


def test_tie_or_none_gives_no_speaker_low():
    tie = source(seg("A", "송금하세요"), seg("B", "이체하세요"))
    assert pick_other_speaker(tie.segments) is None
    r = read_with_lexicon(tie).reading
    assert r.other_speaker is None
    assert r.speaker_confidence == "low"
    assert len(r.demands) == 2  # 상대를 못 골라 전체를 읽는다

    none = read_with_lexicon(source(seg("A", "안녕하세요"))).reading
    assert none.other_speaker is None and none.speaker_confidence == "low"


def test_caps_enforced():
    text = ". ".join(f"계좌로 송금하세요 {i}" for i in range(10))
    r = read_with_lexicon(source(seg("B", text))).reading
    assert len(r.demands) == 6


def test_identity_kinds_from_text():
    from ai.case.lexicon import identity_kinds
    from ai.case.types import IdentityKind

    assert identity_kinds("엄마 나 폰 고장났어") == {IdentityKind.ACQUAINTANCE}
    assert identity_kinds("서울중앙지검 검사입니다") == {IdentityKind.INSTITUTION}
    assert identity_kinds("오늘 날씨가 좋네요") == set()


def test_acquaintance_pattern_ignores_lookalike_words():
    from ai.case.lexicon import ACQUAINTANCE_PATTERN

    for text in ("이모티콘 보내드려요", "엄마손파이 세트", "어머니회 모임", "친구 추천 이벤트", "아버지뻘 되시는"):
        assert not ACQUAINTANCE_PATTERN.search(text), text
    for text in ("엄마 나야", "엄마, 폰 고장났어", "아빠가 사고 났대", "이모한테 말하지 마", "나 아들인데 급해", "친구 계좌로"):
        assert ACQUAINTANCE_PATTERN.search(text), text
