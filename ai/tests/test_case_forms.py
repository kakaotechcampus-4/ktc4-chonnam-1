"""forms: 화자 분리 대신 문장 꼴로 받는 사람 쪽 말을 가린다 (ADR 0008)."""

import pytest

from ai.case import forms
from ai.case.lexicon import INSTITUTION_PATTERN, PUBLIC_INSTITUTION_PATTERN


@pytest.mark.parametrize(
    "sentence",
    [
        "검찰이라면서 송금하라고요?",
        "보내라고 하셨잖아요",
        "검찰이라고 하더라고요",
        "옮기라는 거예요?",
        "보내래요",
    ],
)
def test_quotation(sentence):
    assert forms.is_quotation(sentence)
    assert forms.is_recipient_side(sentence)


def test_self_intro_with_rago_hamnida_is_not_quotation():
    s = "검찰청 수사관 김민수라고 합니다"
    assert not forms.is_quotation(s)
    assert forms.asserts_institution(s, INSTITUTION_PATTERN)


@pytest.mark.parametrize(
    "sentence,recipient",
    [
        ("보내야 돼요?", True),
        ("이름이 뭐예요?", True),
        ("주민번호가 뭐예요?", True),  # "주민" 의 "주" 는 높임이 아니다
        ("제가 보낼게요", True),
        ("제 번호는 010-9999-8888 입니다", True),
        ("인증번호 불러 주시겠어요?", False),
        ("성함이 어떻게 되세요?", False),
        ("제가 알려드리는 계좌로 보내세요", False),
        ("입금해 주면 오늘 갚을게", False),
    ],
)
def test_recipient_side(sentence, recipient):
    assert forms.is_recipient_side(sentence) is recipient


@pytest.mark.parametrize(
    "sentence,asks",
    [
        ("안전계좌로 옮기셔야 합니다", True),
        ("이름과 주민번호를 입력하시면 됩니다", True),
        ("생년월일 여섯 자리만 말씀 부탁 드리겠습니다", True),
        ("엄마 돈 좀 보내줄 수 있어?", True),
        ("현금을 주시는 건가요?", False),  # 높임이 있어도 되묻기다
        ("저희가 확인서도 보내드릴 겁니다", False),
    ],
)
def test_request(sentence, asks):
    assert forms.is_request(sentence) is asks


def test_sentence_at_and_sentences_keep_dotted_numbers():
    text = "금융감독원 수사관입니다. 확인은 02.1234.5678로 하세요"
    assert forms.sentence_at(text, 0, 5) == "금융감독원 수사관입니다."
    assert forms.sentence_at(text, 20, 25) == "확인은 02.1234.5678로 하세요"
    assert forms.sentences(text) == ["금융감독원 수사관입니다.", "확인은 02.1234.5678로 하세요"]


@pytest.mark.parametrize(
    "sentence",
    ["50만원만 보내줄래요?", "계좌로 보내 줄래요?", "이 번호로 전화해줄래요?"],
)
def test_julraeyo_asks_the_listener(sentence):
    """"줄래요?" 는 상대에게 하는 부탁이다. 옮긴 말 꼴 "래요" 로 거르면 실제 요청이 빠진다 (PR #147 리뷰)."""
    assert not forms.is_quotation(sentence)
    assert not forms.is_recipient_side(sentence)
    assert forms.is_request(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["보내래요?", "보낸대요?", "보내라고요?", "보내야 돼요?"],
)
def test_echoing_question_stays_recipient_side(sentence):
    assert forms.is_recipient_side(sentence)
    assert not forms.is_request(sentence)


# ── PR #147 리뷰 후속: 기관 자기소개·부탁 꼴을 좁혀 고친다 ──────────────────


@pytest.mark.parametrize(
    "sentence",
    [
        "저는 국세청 직원이 아니라 민원인입니다",
        "저는 서울중앙지검 수사관이 아니라 피해자입니다",
        "제가 경찰에 신고한 사람입니다",
        "저는 국세청에 민원 넣은 사람입니다",
        "저는 건강보험공단 가입자입니다",
        "저는 국세청 납세자입니다",
        "저는 국민연금 수급자예요",
    ],
)
def test_sentence_about_institution_is_not_self_intro(sentence):
    """직원이 아니라고 하거나 기관을 이용하는 사람의 말은 기관 자기소개가 아니다."""
    assert not forms.asserts_institution(sentence, INSTITUTION_PATTERN)
    assert not forms.asserts_institution(sentence, PUBLIC_INSTITUTION_PATTERN)


@pytest.mark.parametrize(
    "sentence",
    ["제가 대신 입금해 줄까요?", "제가 보내줄래요?", "내가 보내줘?", "제가 상품권 사 줄까요?"],
)
def test_first_person_offer_is_recipient_side(sentence):
    assert forms.is_recipient_side(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "제가 알려드린 계좌로 보내주실래요?",
        "제 번호로 보내줄래요?",
        "내가 급해서 그런데 50만원만 보내줄래?",
    ],
)
def test_request_with_first_person_is_not_offer(sentence):
    assert not forms.is_recipient_side(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["돈 좀 보낼래요?", "상품권 좀 사 볼래요?", "이 앱 설치해 볼래요?", "신분증 사진 좀 보낼래요?", "돈 좀 보내 볼래?"],
)
def test_asking_with_l_raeyo_is_not_recipient_side(sentence):
    """"보낼래요?" 는 상대에게 묻는 말이다. "보내래요" 와 달리 앞 글자에 ㄹ 받침이 있다."""
    assert not forms.is_recipient_side(sentence)
    # 부탁 꼴은 아니라 주의까지만 쓰고 하한(L-3)에는 쓰지 않는다
    assert not forms.is_request(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["돈 보낼래요.", "제가 보낼래요?", "보내지 말래요", "보내지 말래요?"],
)
def test_own_intention_or_reported_ban_stays_recipient_side(sentence):
    assert forms.is_recipient_side(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "돈 좀 보낼 수 있어?",
        "인증번호 나한테 보낼 수 있어?",
        "오늘 안에 송금할 수 있죠?",
        "급한데 돈 좀 보내면 안 될까?",
        "오늘까지 50만원만 입금 가능해요?",
        "이 계좌로 입금 가능할까요?",
    ],
)
def test_asking_if_listener_can_is_not_recipient_side(sentence):
    assert not forms.is_recipient_side(sentence)
    assert not forms.is_request(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["제가 보낼 수 있어요?", "제가 입금 가능해요?", "보내면 안 돼요?", "보내면 안 되나요?", "보내야 해요?"],
)
def test_asking_about_own_action_stays_recipient_side(sentence):
    assert forms.is_recipient_side(sentence)


@pytest.mark.parametrize("sentence", ["송금 좀 해 주겠어요?", "돈 좀 보내 주겠니?"])
def test_jugesseoyo_asks_the_listener(sentence):
    assert not forms.is_recipient_side(sentence)
    # 새로 넣는 꼴은 다른 물음과 같이 주의까지만 쓰고 하한(L-3)에는 쓰지 않는다
    assert not forms.is_request(sentence)


def test_first_person_jugesseoyo_is_offer():
    assert forms.is_recipient_side("제가 해 주겠어요?")


@pytest.mark.parametrize(
    "sentence, pattern",
    [
        ("저는 국세청에서 나온 직원입니다", PUBLIC_INSTITUTION_PATTERN),
        ("저는 국세청에서 근무하는 김민수입니다", PUBLIC_INSTITUTION_PATTERN),
        ("저는 건강보험공단에서 일하는 직원입니다", PUBLIC_INSTITUTION_PATTERN),
        ("저는 경찰청에서 근무하는 김민수 경위입니다", INSTITUTION_PATTERN),
    ],
)
def test_affiliated_with_eseo_is_self_intro(sentence, pattern):
    assert forms.asserts_institution(sentence, pattern)


@pytest.mark.parametrize(
    "sentence",
    ["저는 국세청에서 나온 서류를 받았어요", "국세청에서 일하는 친구가 있어요", "경찰청에서 근무하는 사람을 만났어요"],
)
def test_eseo_without_self_intro_ending_is_not_self_intro(sentence):
    assert not forms.asserts_institution(sentence, INSTITUTION_PATTERN)
    assert not forms.asserts_institution(sentence, PUBLIC_INSTITUTION_PATTERN)


@pytest.mark.parametrize(
    "sentence",
    ["저는 국세청 직원이거든요", "저는 국세청 직원이며 미납 건으로 문자드립니다", "나는 국세청 직원이야", "저는 세무서 직원이라서요"],
)
def test_casual_self_intro_ending(sentence):
    assert forms.asserts_institution(sentence, PUBLIC_INSTITUTION_PATTERN)


@pytest.mark.parametrize(
    "sentence",
    ["나는 경찰 직원이야?", "경찰이야?", "경찰이며 검찰이며 다 전화 왔어요", "경찰이거든요"],
)
def test_casual_ending_right_after_institution_or_as_question_is_not_self_intro(sentence):
    assert not forms.asserts_institution(sentence, INSTITUTION_PATTERN)


def test_casual_ending_without_first_person_is_not_self_intro():
    """구두점 없는 안내 방송의 "이며" 를 자기소개로 읽지 않는다."""
    assert not forms.asserts_institution("금융감독원 안내 사항이며 상담 바랍니다", INSTITUTION_PATTERN)


@pytest.mark.parametrize("sentence", ["저는 김민수 검사입니다", "저는 검사입니다"])
def test_prosecutor_self_intro_with_first_person(sentence):
    assert forms.asserts_institution(sentence, INSTITUTION_PATTERN)


def test_exam_sentence_without_first_person_is_not_self_intro():
    assert not forms.asserts_institution("오늘은 정기 검사입니다", INSTITUTION_PATTERN)
