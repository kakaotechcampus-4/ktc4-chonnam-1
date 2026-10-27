"""verify_call: v3 §5.5 V-1~V-8 (통화)."""

from __future__ import annotations

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
from ai.case.verify import verify_call


def _source(*lines: tuple[str, str], split_failed: bool = False) -> ReadingSource:
    return ReadingSource(
        kind=InputKind.AUDIO,
        segments=[Segment(side=Side.UNKNOWN, speaker=s, text=t) for s, t in lines],
        other_speaker="화자2",
        speaker_split_failed=split_failed,
    )


def _reading(**kw) -> ClaimReading:
    kw.setdefault("other_speaker", "화자2")
    kw.setdefault("speaker_confidence", "high")
    return ClaimReading(**kw)


def _verify(source, reading):
    return verify_call(source, reading, derived_by="llm")


def _codes(result):
    return [e.code for e in result.accepted]


def _reasons(result):
    return [r.reason for r in result.rejected]


PROSECUTOR = ("화자2", "서울중앙지검 금융범죄수사팀입니다")
TRANSFER = ("화자2", "잔액을 안전한 곳으로 옮겨 두셔야 합니다")


def test_institution_and_money_pass_with_floor_flags():
    src = _source(PROSECUTOR, TRANSFER)
    r = _verify(
        src,
        _reading(
            identity=[
                IdentityClaim(
                    kind=IdentityKind.INSTITUTION,
                    name="서울중앙지검",
                    quote=PROSECUTOR[1],
                )
            ],
            demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=TRANSFER[1])],
        ),
    )
    assert _codes(r) == [EvidenceCode.INSTITUTION_CLAIM, EvidenceCode.MONEY_REQUEST]
    assert all(e.origin is Origin.TRANSCRIPT and e.derived_by == "llm" for e in r.accepted)
    assert all(e.counts_for_floor for e in r.accepted)
    assert r.claims.identity[0].listed_institution is True
    assert r.claims.demands[0].kind is DemandKind.MONEY_TRANSFER
    assert not r.speaker_split_failed


def test_derived_by_is_passed_through():
    r = verify_call(
        _source(TRANSFER),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=TRANSFER[1])]),
        derived_by="lexicon",
    )
    assert r.accepted[0].derived_by == "lexicon"


def test_n3_bank_is_not_institution_claim():
    quote = "국민은행 고객센터입니다"
    src = _source(("화자2", quote + ". 이번 달 카드 대금이 미납되어 오늘까지 이체 부탁드립니다"))
    r = _verify(
        src,
        _reading(
            identity=[IdentityClaim(kind=IdentityKind.INSTITUTION, name="국민은행", quote=quote)]
        ),
    )
    assert EvidenceCode.INSTITUTION_CLAIM not in _codes(r)
    assert r.claims.identity[0].listed_institution is False


def test_n4_second_sentence_money_transfer_dropped_by_notice_rule():
    quote = "고객님 명의 계좌가 범죄에 연루됐습니다"
    src = _source(("화자2", "금융감독원 수사관입니다. " + quote))
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
        _source(("화자2", q)),
        _reading(demands=[DemandClaim(kind=DemandKind.PERSONAL_INFO, quote=q)]),
    )
    assert _codes(r) == [EvidenceCode.INFO_REQUEST]


def test_doemnida_after_myeon_is_a_request():
    ok = "이름과 주민번호를 입력하시면 됩니다"
    bad = "내일 발송됩니다"
    r = _verify(
        _source(("화자2", ok), ("화자2", bad)),
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
        _source(("화자1", mine), PROSECUTOR),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=mine)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["self_side"]


def test_v2_allows_everyone_when_split_failed():
    mine = "제가 돈을 보내겠습니다 지금"
    r = _verify(
        _source(("화자1", mine), split_failed=True),
        _reading(demands=[DemandClaim(kind=DemandKind.MONEY_TRANSFER, quote=mine)]),
    )
    assert _codes(r) == [EvidenceCode.MONEY_REQUEST]
    assert r.speaker_split_failed


def test_v4_negated_demand_rejected():
    quote = "앱 설치를 요구하지 않습니다"
    r = _verify(
        _source(("화자2", "저희는 " + quote)),
        _reading(demands=[DemandClaim(kind=DemandKind.APP_INSTALL, quote=quote)]),
    )
    assert _codes(r) == []
    assert _reasons(r) == ["negated"]


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
        _source(("화자2", "가 나 다 라 마")),
        _reading(pretexts=[PretextClaim(quote="가 나 다")]),
    )
    assert _reasons(r) == ["too_short"]


def test_v1_whitespace_collapsed_quote_is_accepted():
    r = _verify(
        _source(("화자2", "잔액을   안전한 곳으로\n옮겨 두셔야 합니다")),
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
        _source(("화자2", "엄마 나야 휴대폰이 고장 났어")),
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
        _source(("화자2", q)),
        _reading(demands=[DemandClaim(kind=DemandKind.VISIT_LINK, quote=q)]),
    )
    assert r.accepted == []
    assert [d.kind for d in r.claims.demands] == [DemandKind.VISIT_LINK]


def test_non_floor_code_flagged_false():
    q = "확인은 02-1234-5678로 하세요"
    r = _verify(
        _source(("화자2", q)),
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


def test_v8_low_confidence_marks_split_failed():
    r = _verify(_source(PROSECUTOR), _reading(speaker_confidence="low"))
    assert r.speaker_split_failed


def test_v8_missing_or_unknown_speaker():
    assert _verify(_source(PROSECUTOR), _reading(other_speaker="화자9")).speaker_split_failed
    src = _source(PROSECUTOR).model_copy(update={"other_speaker": None})
    assert _verify(src, _reading(other_speaker=None)).speaker_split_failed


def test_v8_source_flag_stays_true():
    r = _verify(_source(PROSECUTOR, split_failed=True), _reading())
    assert r.speaker_split_failed
