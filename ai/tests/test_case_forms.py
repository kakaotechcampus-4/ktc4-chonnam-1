"""forms: 화자 분리 대신 문장 꼴로 받는 사람 쪽 말을 가린다 (ADR 0008)."""

import pytest

from ai.case import forms
from ai.case.lexicon import INSTITUTION_PATTERN, PUBLIC_INSTITUTION_PATTERN, introduces_institution


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
    ["제가 대신 입금해 줄까요?", "내가 보내줄까?", "제가 입금해 드릴까요?", "제가 상품권 사 줄까요?"],
)
def test_first_person_offer_is_recipient_side(sentence):
    assert forms.is_recipient_side(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "제가 알려드린 계좌로 보내주실래요?",
        "제 번호로 보내줄래요?",
        "내가 급해서 그런데 50만원만 보내줄래?",
        "내가 말한 계좌로 입금해 줄래?",
        "내가 보낸 계좌로 보내줘?",
        "제가 알려드린 계좌로 송금해 줄래요?",
        "제가 보낸 링크로 들어가 줄래요?",
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


# ── aa81f9a 검토에서 찾은 회귀: 넓힌 꼴을 좁힌다 ──────────────────


@pytest.mark.parametrize(
    "sentence",
    [
        "금융감독원 김대리입니다 고객님 명의로 계좌가 개설됐습니다",
        "서울중앙지검 수사관입니다 고객님 사건 때문에",
        "서울중앙지검 김민수 수사관입니다 피해자 조사 때문에",
        "경찰청 사이버수사대입니다 신고자분 맞으시죠",
        "서울중앙지검 수사관입니다 아니 제 말 좀 들어보세요",
        "검찰청 수사관인데요 아니 그게 아니고요",
        "금감원에서 연락드렸습니다 고객님",
        "금융감독원에서 전화드렸습니다 피해자분",
        "국세청 담당자입니다 납세자 본인 맞으시죠",
        "건강보험공단 직원입니다 가입자 본인이시죠",
    ],
)
def test_words_after_self_intro_do_not_cancel_it(sentence):
    """구두점 없는 받아쓰기에서 자기소개 뒤에 이어지는 말("고객님", "아니")로 자기소개를 지우지 않는다."""
    assert introduces_institution(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["서울경찰청에 소속된 수사관입니다", "검찰청에 계시는 김수사관입니다", "금감원에 재직 중인 김대리입니다"],
)
def test_membership_with_e_is_self_intro(sentence):
    assert introduces_institution(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "저는 경찰서 가는 길입니다",
        "저는 우체국 앞이에요",
        "저는 세무서 근처인데요",
        "저는 우체국 가는 길이라서요",
        "나는 경찰서 앞이야",
        "저는 우체국 앞이거든요",
        "저는 국세청 직원 친구입니다",
        "저는 경찰 시험 준비생이에요",
        "저는 경찰 아들입니다",
        "저는 경찰청에서 일하는 친구입니다",
        "저는 국세청에서 나온 서류 때문에 전화한 사람입니다",
        "저는 내일 건강 검사입니다",
        "제가 오늘 코로나 검사입니다",
        "제가 국세청 직원이에요?",
        "제가 경찰이에요?",
    ],
)
def test_first_person_without_title_is_not_self_intro(sentence):
    """"저는" 으로 시작하면 받는 사람 말일 수 있어, 기관 뒤에 직함이 와야 자기소개로 본다."""
    assert not introduces_institution(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "저는 서울중앙지검 수사관입니다",
        "저는 국세청 직원입니다",
        "저는 금융감독원 김대리입니다",
        "저는 경찰입니다",
        "저는 금감원 소속입니다",
        "제가 경찰청 사이버수사대 팀장인데요",
    ],
)
def test_first_person_with_title_is_self_intro(sentence):
    assert introduces_institution(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["내일 입금하면 안 될까요?", "입금 확인 가능해요?", "카드 결제 가능해요?", "아빠가 보내 주겠죠?", "은행에서 처리해 주겠죠?"],
)
def test_everyday_question_stays_recipient_side(sentence):
    """돈을 집어 말하지 않는 "안 될까요?", "가능해요?" 와 "주겠죠?" 는 부탁으로 보지 않는다."""
    assert forms.is_recipient_side(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["어머니, 고객님 성함이 어떻게 되세요?", "아버님, 명의자분 생년월일 말씀해 주세요", "어머니 저희 고객센터 상담원입니다"],
)
def test_customer_service_address_is_not_acquaintance(sentence):
    """상담원이 부르는 "어머니" 는 가족이라는 주장이 아니다."""
    assert not forms.asserts_acquaintance(sentence)


@pytest.mark.parametrize("sentence", ["엄마, 폰 고장 났어", "엄마 나 급해", "아빠 나 아들인데", "엄마, 폰 고장 나서 고객센터에 맡겼어"])
def test_family_address_stays_acquaintance(sentence):
    assert forms.asserts_acquaintance(sentence)
