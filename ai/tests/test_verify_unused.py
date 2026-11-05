"""원문에서 찾았지만 근거로 바꿀 수 없어 쓰지 않은 주장을 사유별로 센다.

rejected(계약에 실리는 버림 사유)와 달리 내부 진단용이다. 통화 10건 재측정에서
읽은 주장이 버림 기록도 없이 사라져(10번: 신원 2·요구 1 → 근거 0) 원인을 알 수 없었다.
"""
from ai.case.types import ClaimReading, InputKind, ReadingSource, Segment, Side
from ai.case.verify import verify_call


def _src(*texts):
    return ReadingSource(
        kind=InputKind.AUDIO, segments=[Segment(side=Side.UNKNOWN, text=t) for t in texts]
    )


def _verify(texts, reading):
    return verify_call(_src(*texts), ClaimReading.model_validate(reading), derived_by="llm")


def test_unlisted_institution_is_counted_as_unused():
    v = _verify(
        ["지동협은행 김민수 대리입니다"],
        {"identity": [{"kind": "institution", "name": "지동협은행", "quote": "지동협은행 김민수 대리입니다"}]},
    )
    assert v.accepted == []
    assert v.rejected == []
    assert v.unused == ["identity:unlisted_institution"]


def test_company_and_other_identity_are_counted_as_unused():
    v = _verify(
        ["저희는 행복캐피탈 상담팀입니다", "담당 직원 김민수입니다"],
        {
            "identity": [
                {"kind": "company", "name": "행복캐피탈", "quote": "저희는 행복캐피탈 상담팀입니다"},
                {"kind": "other", "name": "직원", "quote": "담당 직원 김민수입니다"},
            ]
        },
    )
    assert v.unused == ["identity:company", "identity:other"]


def test_demand_without_evidence_code_is_counted_as_unused():
    v = _verify(
        ["서류를 준비해서 내일까지 제출해 주세요"],
        {"demands": [{"kind": "other", "quote": "서류를 준비해서 내일까지 제출해 주세요"}]},
    )
    assert v.accepted == []
    assert v.unused == ["demand:other"]


def test_used_claims_are_not_unused():
    v = _verify(
        ["서울중앙지검 김민수 수사관입니다", "안전계좌로 지금 이체하셔야 합니다"],
        {
            "identity": [{"kind": "institution", "name": "서울중앙지검", "quote": "서울중앙지검 김민수 수사관입니다"}],
            "demands": [{"kind": "money_transfer", "quote": "안전계좌로 지금 이체하셔야 합니다"}],
        },
    )
    assert len(v.accepted) == 2
    assert v.unused == []
