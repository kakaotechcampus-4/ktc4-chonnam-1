"""verify_call: v3 §5.5 V-1~V-7 (통화). V-2 는 화자 대신 문장 꼴을 본다 (ADR 0008)."""

from __future__ import annotations

import pytest

from ai.case.types import (
    BlockerClaim,
    ClaimReading,
    DemandClaim,
    DemandKind,
    EvidenceCode,
    IdentityClaim,
    IdentityKind,
    InputKind,
    Origin,
    PretextClaim,
    ReadingSource,
    Segment,
    Side,
)
from ai.case import forms
from ai.case.lexicon import read_with_lexicon
from ai.case.verify import verify_call
from ai.types import FailureCode


def _source(*lines: str) -> ReadingSource:
    return ReadingSource(
        kind=InputKind.AUDIO,
        segments=[Segment(side=Side.UNKNOWN, text=t) for t in lines],
    )


def _reading(**kw) -> ClaimReading:
    return ClaimReading(**kw)


def _verify(source, reading):
    return verify_call(source, reading, derived_by="llm")


def _codes(result):
    return [e.code for e in result.accepted]


def _reasons(result):
    return [r.reason for r in result.rejected]


def _floor(result):
    return {e.code: e.counts_for_floor for e in result.accepted}


PROSECUTOR = "서울중앙지검 금융범죄수사팀입니다"
TRANSFER = "잔액을 안전한 곳으로 옮겨 두셔야 합니다"


def test_institution_and_money_pass_with_floor_flags():
    src = _source(PROSECUTOR, TRANSFER)
    r = _verify(
        src,
        _reading(
            identity=[
                IdentityClaim(
                    kind=IdentityKind.INSTITUTION,
                    name="서울중앙지검",
                    quote=PROSECUTOR,
                )
            ],
            demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=TRANSFER)],
        ),
    )
    assert _codes(r) == [EvidenceCode.INSTITUTION_CLAIM, EvidenceCode.MONEY_REQUEST]
    assert all(e.origin is Origin.TRANSCRIPT and e.derived_by == "llm" for e in r.accepted)
    assert all(e.counts_for_floor for e in r.accepted)
    assert r.claims.identity[0].listed_institution is True
    assert r.claims.demands[0].kind is DemandKind.MONEY_TRANSFER


def test_derived_by_is_passed_through():
    r = verify_call(
        _source(TRANSFER),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=TRANSFER)]),
        derived_by="lexicon",
    )
    assert r.accepted[0].derived_by == "lexicon"


def test_n3_bank_is_not_institution_claim():
    quote = "국민은행 고객센터입니다"
    src = _source(quote + ". 이번 달 카드 대금이 미납되어 오늘까지 이체 부탁드립니다")
    r = _verify(
        src,
        _reading(
            identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="국민은행", quote=quote)]
        ),
    )
    assert EvidenceCode.INSTITUTION_CLAIM not in _codes(r)
    assert r.claims.identity[0].listed_institution is False


@pytest.mark.parametrize(
    "name",
    ["국세청", "서초세무서", "관세청", "국민건강보험공단", "건강보험공단", "국민연금공단", "고용노동부", "우체국", "출입국관리사무소"],
)
def test_public_institution_cautions_but_never_counts_for_floor(name):
    """자주 사칭되는 공공기관은 주의까지만 쓴다. 자기소개 + 부탁 꼴이어도 L-3 근거가 아니다 (ADR 0010)."""
    quote = f"{name} 담당자입니다"
    info = "계좌번호를 불러 주세요"
    r = _verify(
        _source(quote, info),
        _reading(
            identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name=name, quote=quote)],
            demands=[DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=info)],
        ),
    )
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: False, EvidenceCode.INFO_REQUEST: True}
    assert r.claims.identity[0].listed_institution is True


def test_public_institution_found_in_quote_replaces_model_name():
    quote = "[국세청] 고객님 앞으로"
    r = _verify(
        _source(quote + " 미납된 세금이 있습니다"),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="세금 담당", quote=quote)]),
    )
    assert _codes(r) == [EvidenceCode.INSTITUTION_CLAIM]
    assert r.claims.identity[0].name == "국세청"


def test_public_institution_name_not_in_source_is_not_trusted():
    quote = "고객님 앞으로 미납된 세금이"
    r = _verify(
        _source(quote + " 있습니다"),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="국세청", quote=quote)]),
    )
    assert EvidenceCode.INSTITUTION_CLAIM not in _codes(r)
    assert r.claims.identity[0].listed_institution is False


def test_investigative_institution_still_wins_over_public_one():
    quote = "서울중앙지검 수사관입니다"
    r = _verify(
        _source("국세청 자료를 확인했습니다", quote),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="서울중앙지검", quote=quote)]),
    )
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: True}


def test_n4_second_sentence_money_transfer_dropped_by_notice_rule():
    quote = "고객님 명의 계좌가 범죄에 연루됐습니다"
    src = _source("금융감독원 수사관입니다. " + quote)
    r = _verify(
        src,
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=quote)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["notice_sentence"]
    assert r.claims.demands == []


def test_question_ending_skips_notice_check():
    q = "수사관입니다 맞으시죠?"
    r = _verify(
        _source(q),
        _reading(demands=[DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=q)]),
    )
    assert _codes(r) == [EvidenceCode.INFO_REQUEST]


def test_doemnida_after_myeon_is_a_request():
    ok = "이름과 주민번호를 입력하시면 됩니다"
    bad = "내일 발송됩니다"
    r = _verify(
        _source(ok, bad),
        _reading(
            demands=[
                DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=ok),
                DemandClaim(kind=DemandKind.OTHER, quote=bad),
            ]
        ),
    )
    assert _codes(r) == [EvidenceCode.INFO_REQUEST]
    assert _reasons(r) == ["notice_sentence"]


def test_v2_self_side_quote_rejected():
    mine = "제가 돈을 보내겠습니다 지금"
    r = _verify(
        _source(mine, PROSECUTOR),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=mine)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]


# ── V-2: 받는 사람이 되풀이한 말 (ADR 0008) ─────────────────────


@pytest.mark.parametrize(
    "echo",
    [
        "검찰이라면서 송금하라고요?",
        "네? 송금하라고요?",
        "돈을 옮기라는 거예요?",
        "보내야 돼요?",
        "제가 보낼게요",
        "계좌번호 알려드려야 하나요?",
    ],
)
def test_v2_victim_echo_of_demand_rejected(echo):
    r = _verify(
        _source(PROSECUTOR, echo),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=echo)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]


@pytest.mark.parametrize("echo", ["검찰이요?", "검찰이라면서요", "경찰이세요?", "경찰에 신고할게요"])
def test_v2_victim_echo_of_identity_rejected(echo):
    r = _verify(
        _source(echo),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="검찰", quote=echo)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]
    assert r.claims.identity == []


def test_v2_same_quote_said_by_caller_then_echoed_uses_caller_sentence():
    # 상대의 "송금하세요" 를 받는 사람이 "송금하세요?" 로 되물어도 상대 문장으로 받아들인다
    r = _verify(
        _source("네? 지금 바로 송금하세요?", "지금 바로 송금하세요"),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote="지금 바로 송금하세요")]),
    )
    assert _codes(r) == [EvidenceCode.MONEY_REQUEST]
    assert _floor(r) == {EvidenceCode.MONEY_REQUEST: True}


def test_polite_request_question_is_accepted_and_counts():
    q = "주민번호 앞자리가 어떻게 되세요?"
    r = _verify(_source(q), _reading(demands=[DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=q)]))
    assert _floor(r) == {EvidenceCode.INFO_REQUEST: True}


# ── 하한 꼴: 자기소개·부탁 (ADR 0008) ───────────────────────────


@pytest.mark.parametrize(
    "intro",
    [
        "서울중앙지검 금융범죄수사팀 김○○ 수사관입니다",
        "금융감독원에서 연락드렸습니다",
        "검찰청인데 잠시 통화 괜찮으세요?",
        "서울지방경찰청에 있는 사이버수사대입니다",
        "저희 서울중앙지방검찰청으로 오셔서 확인하셔야 합니다",
    ],
)
def test_institution_self_intro_counts_for_floor(intro):
    r = _verify(
        _source(intro),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="검찰", quote=intro)]),
    )
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: True}


@pytest.mark.parametrize(
    "mention", ["검찰에서 수사 중인 사건이라", "검찰에서 전화 왔어요", "금감원은 이런 전화 안 합니다"]
)
def test_institution_mention_is_caution_not_floor(mention):
    r = _verify(
        _source(mention),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="검찰", quote=mention)]),
    )
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: False}
    assert r.claims.identity[0].listed_institution is True


@pytest.mark.parametrize(
    "quote,floor",
    [
        ("엄마 나야", True),
        ("엄마, 폰 고장 났어", True),
        ("나 아들인데 급해", True),
        ("우리 아들이 학교에서 그러더라", False),
        ("친구가 잠옷을 사줬는데", False),
    ],
)
def test_acquaintance_floor_only_when_claimed_to_listener(quote, floor):
    r = _verify(
        _source(quote),
        _reading(identity=[IdentityClaim(kind=IdentityKind.ACQUAINTANCE, name="자녀", quote=quote)]),
    )
    assert _floor(r) == {EvidenceCode.ACQUAINTANCE_CLAIM: floor}


@pytest.mark.parametrize(
    "quote,floor",
    [
        ("안전계좌로 옮기셔야 합니다", True),
        ("입금해 주면 오늘 갚을게", True),
        ("애들하고 추억을 보내려면 운동이 좋겠다", False),
        ("저희가 피해자 확인서도 보내드릴 겁니다", False),
    ],
)
def test_demand_floor_only_when_asked_of_listener(quote, floor):
    r = _verify(
        _source(quote),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=quote)]),
    )
    assert _floor(r) == {EvidenceCode.MONEY_REQUEST: floor}


def test_v4_negated_demand_rejected():
    quote = "앱 설치를 요구하지 않습니다"
    r = _verify(
        _source("저희는 " + quote),
        _reading(demands=[DemandClaim(kind=DemandKind.APP_INSTALL, quote=quote)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["negated"]


def test_v4_only_last_clause_is_checked():
    for q in ("걱정하지 않으셔도 되고 앱 설치하세요", "돈을 보내지 마시고 앱 설치하세요"):
        r = _verify(
            _source(q),
            _reading(demands=[DemandClaim(kind=DemandKind.APP_INSTALL, quote=q)]),
        )
        assert _codes(r) == [EvidenceCode.APP_INSTALL_REQUEST], q
        assert r.rejected == []


def test_v4_progressive_go_is_not_a_clause_break():
    q = "앱 설치를 요구하지 않고 있습니다"
    r = _verify(
        _source(q),
        _reading(demands=[DemandClaim(kind=DemandKind.APP_INSTALL, quote=q)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["negated"]


def test_gift_card_evidence_counts_for_floor():
    q = "문화상품권 사서 핀번호 보내주세요"
    r = _verify(
        _source(q),
        _reading(demands=[DemandClaim(kind=DemandKind.GIFT_CARD, quote=q)]),
    )
    assert _codes(r) == [EvidenceCode.GIFT_CARD_REQUEST]
    assert r.accepted[0].counts_for_floor is True


def test_v1_not_in_source_and_too_short():
    r = _verify(
        _source(PROSECUTOR),
        _reading(
            pretexts=[PretextClaim(quote="없는 문장입니다 정말로"), PretextClaim(quote="수사 중")],
        ),
    )
    assert _reasons(r) == ["not_in_source", "too_short"]
    assert r.claims.pretexts == []


def test_v1_min_length_excludes_spaces():
    r = _verify(
        _source("가 나 다 라 마"),
        _reading(pretexts=[PretextClaim(quote="가 나 다")]),
    )
    assert _reasons(r) == ["too_short"]


def test_v1_whitespace_collapsed_quote_is_accepted():
    r = _verify(
        _source("잔액을   안전한 곳으로\n옮겨 두셔야 합니다"),
        _reading(
            demands=[
                DemandClaim(
                    kind=DemandKind.MONEY_TRANSFER,
                    quote="잔액을 안전한 곳으로 옮겨 두셔야 합니다",
                )
            ]
        ),
    )
    assert _codes(r) == [EvidenceCode.MONEY_REQUEST]
    assert r.rejected == []


def test_acquaintance_and_blocker_evidence():
    r = _verify(
        _source("엄마 나야 휴대폰이 고장 났어"),
        _reading(
            identity=[
                IdentityClaim(kind=IdentityKind.ACQUAINTANCE, name="자녀", quote="엄마 나야")
            ],
            blockers=[BlockerClaim(quote="휴대폰이 고장 났어")],
        ),
    )
    assert _codes(r) == [EvidenceCode.ACQUAINTANCE_CLAIM, EvidenceCode.CONTACT_BLOCKER]
    assert r.claims.blockers == ["휴대폰이 고장 났어"]


def test_visit_link_has_no_evidence_code_but_stays_in_view():
    q = "주소창에 에스피오 대시 케이스 닷컴을 입력하세요"
    r = _verify(
        _source(q),
        _reading(demands=[DemandClaim(kind=DemandKind.VISIT_LINK, quote=q)]),
    )
    assert r.accepted == []
    assert [d.kind for d in r.claims.demands] == [DemandKind.VISIT_LINK]


def test_non_floor_code_flagged_false():
    q = "확인은 02-1234-5678로 하세요"
    r = _verify(
        _source(q),
        _reading(demands=[DemandClaim(kind=DemandKind.CALL_NUMBER, quote=q)]),
    )
    assert r.accepted[0].code is EvidenceCode.CALL_REQUEST
    assert r.accepted[0].counts_for_floor is False


def test_v7_spoken_host_keeps_host_only():
    r = _verify(
        _source(PROSECUTOR),
        _reading(spoken_hosts=["Spo-Case.com/case?name=x", "https://a.kr/x", "그냥 말"]),
    )
    assert r.spoken_hosts == ["spo-case.com", "a.kr"]
    assert _reasons(r) == ["not_url"]


def test_v7_uses_message_link_host_rule():
    # 점으로 이어 부른 전화번호·숫자는 주소가 아니다 (ai.links.valid_host)
    r = _verify(_source(PROSECUTOR), _reading(spoken_hosts=["010.1234.5678", "3.5"]))
    assert r.spoken_hosts == []
    assert _reasons(r) == ["not_url", "not_url"]


def test_r15_name_not_in_transcript_is_not_trusted():
    # 모델이 name 에 "경찰청"을 적었어도 대본에 경찰이 없으면 기관 주장이 아니다
    quote = "고객님 명의로 사건이 접수되어서요"
    info = "주민번호 앞자리를 불러 주세요"
    r = _verify(
        _source(quote, info),
        _reading(
            identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="경찰청", quote=quote)],
            demands=[DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=info)],
        ),
    )
    assert EvidenceCode.INSTITUTION_CLAIM not in _codes(r)
    assert EvidenceCode.INFO_REQUEST in _codes(r)
    assert r.claims.identity[0].listed_institution is False


def test_r15_name_in_transcript_is_trusted_and_intro_counts():
    quote = "고객님 명의로 사건이 접수되어서요"
    src = _source("경찰에서 연락드렸습니다", quote)
    r = _verify(
        src,
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="경찰청", quote=quote)]),
    )
    assert EvidenceCode.INSTITUTION_CLAIM in _codes(r)
    assert r.claims.identity[0].name == "경찰청"
    # 인용에는 기관명이 없지만 "경찰에서 연락드렸습니다" 가 자기소개 꼴이라 하한에 쓴다
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: True}


def test_r15_name_only_in_echo_is_not_trusted():
    quote = "고객님 명의로 사건이 접수되어서요"
    src = _source("경찰이세요?", quote)
    r = _verify(
        src,
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="경찰청", quote=quote)]),
    )
    assert EvidenceCode.INSTITUTION_CLAIM not in _codes(r)


def test_not_in_source_quote_text_is_not_kept():
    r = _verify(
        _source(PROSECUTOR),
        _reading(pretexts=[PretextClaim(quote="없는 문장입니다 정말로")]),
    )
    assert _reasons(r) == ["not_in_source"]
    assert r.rejected[0].quote == ""


def test_obligation_doemnida_is_a_request_but_status_notice_is_not():
    # PR #114 리뷰: "하셔야 됩니다" 는 요구, "입금됩니다" 는 상태 알림이다
    ok = "안전한 계좌로 송금하셔야 됩니다"
    notice = "고객님 계좌로 30만원이 입금됩니다"
    r = _verify(
        _source(ok, notice),
        _reading(
            demands=[
                DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=ok),
                DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=notice),
            ]
        ),
    )
    assert _codes(r) == [EvidenceCode.MONEY_REQUEST]
    assert _floor(r) == {EvidenceCode.MONEY_REQUEST: True}
    assert _reasons(r) == ["notice_sentence"]


@pytest.mark.parametrize(
    "quote",
    ["안내에 따라 송금해 주세요", "상담 방법대로 앱을 설치해 주세요", "이체 내역 확인 후 다시 보내 주세요"],
)
def test_v4_words_like_annae_before_the_action_do_not_negate(quote):
    # PR #114 리뷰: 절 안의 아무 낱말('안내'·'방법'·'내역') 뒤를 보면 요구가 부정으로 잡혔다
    kind = DemandKind.APP_INSTALL if "앱" in quote else DemandKind.MONEY_TRANSFER
    r = _verify(_source(quote), _reading(demands=[DemandClaim(kind=kind, quote=quote)]))
    assert r.rejected == [], quote
    assert len(r.accepted) == 1


@pytest.mark.parametrize(
    ("sentence", "quote", "kind"),
    [
        ("돈을 보내지 마세요", "돈을 보내지 마세요", DemandKind.MONEY_TRANSFER),
        ("앱을 깔지 마세요", "앱을 깔지 마세요", DemandKind.APP_INSTALL),
        ("인증번호는 알려 주지 마십시오", "인증번호는 알려 주지 마십시오", DemandKind.AUTH_CODE),
        ("돈을 보내지 마세요", "돈을 보내", DemandKind.MONEY_TRANSFER),
        ("앱은 설치하실 필요 없습니다", "앱은 설치하실 필요 없습니다", DemandKind.APP_INSTALL),
        ("지금은 안 보내셔도 됩니다", "지금은 안 보내셔도 됩니다", DemandKind.MONEY_TRANSFER),
    ],
)
def test_v4_negated_imperative_rejected(sentence, quote, kind):
    r = _verify(_source(sentence), _reading(demands=[DemandClaim(kind=kind, quote=quote)]))
    assert _codes(r) == []
    assert _reasons(r) == ["negated"]


def test_v4_negated_condition_is_still_a_demand():
    # 끝 서술어가 부정이 아니다. 보내라는 압박이다
    q = "지금 보내지 않으면 계좌가 정지됩니다"
    r = _verify(_source(q), _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=q)]))
    assert "negated" not in _reasons(r)


def test_julraeyo_demand_is_kept_for_floor():
    quote = "50만원만 보내줄래요"
    r = _verify(
        _source(f"엄마 나야. {quote}?"),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=quote)]),
    )
    assert _floor(r) == {EvidenceCode.MONEY_REQUEST: True}
    assert _reasons(r) == []


@pytest.mark.parametrize("sentence", ["50만원만 보내라고요?", "50만원만 보내래요?"])
def test_echoed_demand_is_still_rejected(sentence):
    r = _verify(
        _source(f"엄마 나야. {sentence}"),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote="50만원만 보내")]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]


@pytest.mark.parametrize(
    "name, quote",
    [
        ("국세청", "저는 국세청 직원입니다"),
        ("건강보험공단", "저는 건강보험공단 직원입니다"),
        ("서초세무서", "저는 서초세무서 담당자입니다"),
    ],
)
def test_public_institution_self_intro_with_first_person_is_kept(name, quote):
    """"저는" 이 붙어도 공공기관 자기소개는 받는 사람 말이 아니다. 주의까지만 쓴다 (ADR 0010)."""
    r = _verify(
        _source(quote),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name=name, quote=quote)]),
    )
    assert _floor(r) == {EvidenceCode.INSTITUTION_CLAIM: False}
    assert r.claims.identity[0].listed_institution is True


@pytest.mark.parametrize(
    "sentence",
    [
        "저는 국세청 직원이 아닙니다",
        "저는 국세청 직원 아니에요",
        "저는 국세청에 전화했어요",
        "국세청 직원이라고요?",
    ],
)
def test_public_institution_not_claimed_by_sender_is_rejected(sentence):
    r = _verify(
        _source(sentence),
        _reading(identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="국세청", quote=sentence)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]


@pytest.mark.parametrize(
    "text",
    [
        "제가 경찰에 신고한 사람입니다. 계좌번호 불러 주세요.",
        "저는 서울중앙지검 수사관이 아니라 피해자입니다. 계좌번호 불러 주세요.",
    ],
)
def test_mentioning_institution_with_request_has_no_l3_floor(text):
    source = ReadingSource(
        kind=InputKind.AUDIO,
        segments=[Segment(side=Side.UNKNOWN, text=s) for s in forms.sentences(text)],
    )
    reading = read_with_lexicon(source, FailureCode.TIMEOUT).reading
    verify = verify_call(source, reading, derived_by="lexicon")
    assert not any(e.code == EvidenceCode.INSTITUTION_CLAIM and e.counts_for_floor for e in verify.accepted)


def _fallback_verify(text: str):
    source = ReadingSource(
        kind=InputKind.AUDIO,
        segments=[Segment(side=Side.UNKNOWN, text=s) for s in forms.sentences(text)],
    )
    return verify_call(source, read_with_lexicon(source, FailureCode.TIMEOUT).reading, derived_by="lexicon")


@pytest.mark.parametrize(
    "text",
    [
        "금융감독원 김대리입니다 고객님 명의로 계좌가 개설됐습니다. 계좌번호 불러 주세요.",
        "서울중앙지검 수사관입니다 아니 제 말 좀 들어보세요. 계좌번호 불러 주세요.",
        "금감원에서 연락드렸습니다 고객님. 계좌번호 불러 주세요.",
    ],
)
def test_words_after_self_intro_keep_l3_floor(text):
    assert any(e.code == EvidenceCode.INSTITUTION_CLAIM and e.counts_for_floor for e in _fallback_verify(text).accepted)


@pytest.mark.parametrize(
    "text",
    [
        "저는 내일 건강 검사입니다. 계좌번호 불러 주세요.",
        "나는 경찰서 앞이야. 계좌번호 불러 주세요.",
        "저는 경찰서 가는 길입니다. 계좌번호 불러 주세요.",
        "저는 경찰 아들입니다. 계좌번호 불러 주세요.",
        "저는 경찰청에서 일하는 친구입니다. 계좌번호 불러 주세요.",
        "제가 경찰이에요? 계좌번호 불러 주세요.",
    ],
)
def test_first_person_without_title_has_no_l3_floor(text):
    assert not any(e.code == EvidenceCode.INSTITUTION_CLAIM and e.counts_for_floor for e in _fallback_verify(text).accepted)


def _floors(text: str) -> dict[EvidenceCode, bool]:
    source = ReadingSource(
        kind=InputKind.AUDIO,
        segments=[Segment(side=Side.UNKNOWN, text=s) for s in forms.sentences(text)],
    )
    verify = verify_call(source, read_with_lexicon(source, FailureCode.TIMEOUT).reading, derived_by="lexicon")
    return {e.code: e.counts_for_floor for e in verify.accepted}


@pytest.mark.parametrize(
    "sentence",
    ["신분증 가지고 민원실로 오세요", "오실 때 신분증 챙겨 오세요", "민원실에 방문하셔서 신분증 보여 주세요"],
)
def test_visit_guide_is_not_info_floor(sentence):
    """직접 오라는 안내는 신분증을 넘기라는 요구와 다르다. 주의 근거로는 남긴다."""
    assert _floors(f"서초경찰서 민원실입니다. {sentence}.")[EvidenceCode.INFO_REQUEST] is False


@pytest.mark.parametrize("sentence", ["신분증 사진 찍어서 보내 주세요", "주민번호 불러 주세요", "방문 전에 신분증 찍어서 보내 주세요"])
def test_handing_over_info_keeps_floor(sentence):
    assert _floors(f"서초경찰서 수사관입니다. {sentence}.")[EvidenceCode.INFO_REQUEST] is True


@pytest.mark.parametrize("sentence", ["계약서 사진 보내 줘", "문자로 보내 주세요", "메일 보내 주세요"])
def test_sending_non_money_is_not_money_floor(sentence):
    assert _floors(f"엄마 나야. {sentence}.")[EvidenceCode.MONEY_REQUEST] is False


@pytest.mark.parametrize("sentence", ["50만원 보내 줘", "이 계좌로 사진값 보내 줘", "돈 좀 보내 주세요", "입금해 주세요", "입금하시고 영수증을 팩스로 보내십시오"])
def test_sending_money_keeps_floor(sentence):
    assert _floors(f"엄마 나야. {sentence}.")[EvidenceCode.MONEY_REQUEST] is True
