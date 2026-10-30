"""forms: 화자 분리 대신 문장 꼴로 받는 사람 쪽 말을 가린다 (ADR 0007)."""

import pytest

from ai.case import forms
from ai.case.lexicon import INSTITUTION_PATTERN


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
