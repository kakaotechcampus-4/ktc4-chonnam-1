import pytest

from ai.case.lexicon import (
    INSTITUTION_PATTERN,
    classify_demand,
    demand_kinds,
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


def seg(text):
    return Segment(side=Side.UNKNOWN, text=text)


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
    reading = read_with_lexicon(source(seg(text))).reading
    assert all(i.kind != IdentityKind.INSTITUTION for i in reading.identity)


def test_institution_and_acquaintance_identity():
    r = read_with_lexicon(
        source(seg("금융감독원 수사관입니다"), seg("엄마 나야"))
    ).reading
    kinds = {i.kind for i in r.identity}
    assert kinds == {IdentityKind.INSTITUTION, IdentityKind.ACQUAINTANCE}


def test_ddalgi_not_acquaintance():
    r = read_with_lexicon(source(seg("딸기 주문하셨죠"))).reading
    assert r.identity == []


def test_blockers():
    r = read_with_lexicon(source(seg("엄마 폰이 고장 났어. 통화는 안 돼"))).reading
    assert len(r.blockers) == 2


def test_v1_script_reads_whole_transcript():
    src = source(
        seg("여보세요"),
        seg("금융감독원 수사관 김○○입니다. 고객님 명의 계좌가 범죄에 연루됐습니다"),
        seg("네? 어떻게 해야 하죠"),
        seg("자산 보호를 위해 안전계좌로 옮기셔야 합니다. 확인은 02-1234-5678로 하세요"),
    )
    result = read_with_lexicon(src)
    r = result.reading
    assert result.status == "fallback"
    assert {d.kind for d in r.demands} == {DemandKind.MONEY_TRANSFER, DemandKind.CALL_NUMBER}
    # 인용은 원문 문장 그대로
    assert "금융감독원 수사관 김○○입니다" in [i.quote for i in r.identity]
    assert "확인은 02-1234-5678로 하세요" in [d.quote for d in r.demands]


def test_recipient_side_sentences_are_not_read():
    # 받는 사람이 되묻거나 자기 일을 말한 문장은 읽지 않는다 (ADR 0008)
    r = read_with_lexicon(
        source(seg("검찰이라면서 송금하라고요?"), seg("제가 지금 보낼게요"), seg("보내야 돼요?"))
    ).reading
    assert r.identity == [] and r.demands == []


def test_caps_enforced():
    text = ". ".join(f"계좌로 송금하세요 {i}" for i in range(10))
    r = read_with_lexicon(source(seg(text))).reading
    assert len(r.demands) == 6


def test_caps_prefer_shaped_claims_in_source_order():
    # 상한이 차면 자기소개·부탁 꼴을 먼저 고르고, 고른 것은 원문 순서로 둔다
    refs = [seg(f"검찰 사건 이야기 {i}") for i in range(3)]
    asks = [seg(f"이번 달 송금 내역 {i}") for i in range(6)]
    src = source(*refs, *asks, seg("서울중앙지검 수사관입니다"), seg("안전계좌로 옮기셔야 합니다"))
    r = read_with_lexicon(src).reading
    assert len(r.identity) == 3 and len(r.demands) == 6
    assert r.identity[-1].quote == "서울중앙지검 수사관입니다"
    assert r.demands[-1].quote == "안전계좌로 옮기셔야 합니다"


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


def test_demand_kinds_collects_each_sentence_demand():
    found = demand_kinds("상품권 사서 보내 주세요. 인증번호도 불러 주시고요.\n현금은 안 돼요")
    assert found == {DemandKind.GIFT_CARD, DemandKind.AUTH_CODE, DemandKind.CASH_WITHDRAWAL}
    assert demand_kinds("안녕하세요 날씨가 좋네요") == set()
