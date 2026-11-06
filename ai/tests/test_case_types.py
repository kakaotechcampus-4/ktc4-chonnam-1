import pytest
from pydantic import ValidationError

from ai.case.types import (
    AttachedQuestion,
    ClaimReading,
    ClaimReadingResult,
    ClaimView,
    ClaimViewDemand,
    ClaimViewIdentity,
    Conclusion,
    DemandKind,
    Evidence,
    EvidenceCode,
    IdentityKind,
    InputKind,
    Origin,
    QuestionId,
    ReadingSource,
    RejectedClaim,
    Scenario,
    Segment,
    Side,
    TranscriptResult,
    Unchecked,
    UncheckedItem,
    UncheckedReason,
    VoiceCaseResult,
)
from ai.types import AnalysisStatus, CaseSearchResult, FailureCode


def roundtrip(obj):
    data = obj.model_dump(mode="json")
    again = type(obj).model_validate(data)
    assert again == obj
    return again


def _segment():
    return Segment(side=Side.OTHER, text="안녕", start=0.0, end=1.5)


def test_roundtrip_transcript_and_source():
    t = TranscriptResult(
        segments=[_segment()],
        failure=FailureCode.TIMEOUT,
        stt_model="m",
        audio_seconds=3.0,
    )
    roundtrip(t)
    roundtrip(ReadingSource(kind=InputKind.AUDIO, segments=[_segment()]))


@pytest.mark.parametrize(
    "model,field",
    [
        (TranscriptResult, "speaker_split_failed"),
        (TranscriptResult, "diarize_model"),
        (ReadingSource, "other_speaker"),
        (Segment, "speaker"),
        (ClaimReading, "speaker_confidence"),
    ],
)
def test_speaker_fields_are_gone(model, field):
    # 화자 분리를 없앴다 (ADR 0008). 옛 칸을 넣으면 extra=forbid 로 막힌다
    assert field not in model.model_fields


def test_roundtrip_reading():
    r = ClaimReading.model_validate(
        {
            "identity": [{"kind": "institution", "name": "검찰", "quote": "검찰입니다"}],
            "pretexts": [{"quote": "수사 중"}],
            "demands": [{"kind": "money_transfer", "quote": "옮기세요"}],
            "blockers": [{"quote": "통화 안 돼"}],
            "spoken_hosts": ["a.com"],
        }
    )
    roundtrip(r)
    roundtrip(ClaimReadingResult(status="ok", reading=r))
    roundtrip(ClaimReadingResult(status="fallback", failure=FailureCode.LLM_ERROR))


def test_roundtrip_voice_case_result():
    res = VoiceCaseResult(
        scenario=Scenario.VOICE_PHISHING,
        conclusion=Conclusion.SUSPECTED,
        caution=True,
        floors=["L-3"],
        evidence=[
            Evidence(
                code=EvidenceCode.INSTITUTION_CLAIM,
                origin=Origin.TRANSCRIPT,
                quote="검찰입니다",
                derived_by="llm",
                counts_for_floor=True,
            )
        ],
        claims=ClaimView(
            identity=[
                ClaimViewIdentity(
                    kind=IdentityKind.INSTITUTION,
                    name="검찰",
                    quote="검찰입니다",
                    listed_institution=True,
                )
            ],
            pretexts=["수사 중"],
            demands=[ClaimViewDemand(kind=DemandKind.MONEY_TRANSFER, quote="옮기세요")],
            blockers=[],
        ),
        questions=[
            AttachedQuestion(
                question_id=QuestionId.ALREADY_ACTED, option_ids=["sent_money", "not_yet"]
            )
        ],
        unchecked=[
            Unchecked(
                item=UncheckedItem.TRANSCRIPT_ACCURACY, reason=UncheckedReason.CANNOT_VERIFY
            )
        ],
        similar_cases=CaseSearchResult(status=AnalysisStatus.COMPLETED),
        rejected=[RejectedClaim(kind="demand", quote="x", reason="negated")],
        failures=[FailureCode.TIMEOUT],
    )
    roundtrip(res)


def test_transcript_accuracy_is_unchecked_item():
    assert UncheckedItem("transcript_accuracy") is UncheckedItem.TRANSCRIPT_ACCURACY


@pytest.mark.parametrize(
    "field,item,n",
    [
        ("identity", {"kind": "other", "name": "n", "quote": "q"}, 4),
        ("pretexts", {"quote": "q"}, 4),
        ("demands", {"kind": "other", "quote": "q"}, 7),
        ("blockers", {"quote": "q"}, 4),
        ("spoken_hosts", "a.com", 4),
    ],
)
def test_reading_limits_keep_first_items(field, item, n):
    # 상한을 넘으면 읽기 전체를 버리지 않고 앞에서부터 상한까지만 남긴다.
    # 긴 통화에서 모델이 명분을 4개 이상 뽑아 읽기가 통째로 어휘표로 떨어지던 문제
    items = [item if isinstance(item, str) else {**item, "quote": f"q{i}"} for i in range(n)]
    if isinstance(item, str):
        items = [f"a{i}.com" for i in range(n)]
    reading = ClaimReading.model_validate({field: items})
    kept = getattr(reading, field)
    assert len(kept) == n - 1
    first = kept[0] if isinstance(kept[0], str) else kept[0].quote
    assert first in ("q0", "a0.com")


def test_reading_limits_apply_to_json_too():
    # SDK 의 parse() 는 JSON 문자열을 검증한다. 같은 규칙이 적용돼야 한다
    reading = ClaimReading.model_validate_json(
        '{"pretexts": [{"quote": "a"}, {"quote": "b"}, {"quote": "c"}, {"quote": "d"}, {"quote": "e"}]}'
    )
    assert [p.quote for p in reading.pretexts] == ["a", "b", "c"]


def test_reading_items_still_validated():
    # 개수만 자른다. 항목 모양이 틀리면 여전히 실패다
    with pytest.raises(ValidationError):
        ClaimReading.model_validate({"identity": [{"kind": "bogus", "name": "n", "quote": "q"}]})


def test_reading_limits_boundary_ok():
    ClaimReading.model_validate({"demands": [{"kind": "other", "quote": "q"}] * 6})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Segment.model_validate({"side": "other", "text": "x", "bogus": 1})
