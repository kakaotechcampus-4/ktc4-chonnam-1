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
    return Segment(side=Side.OTHER, speaker="화자1", text="안녕", start=0.0, end=1.5)


def test_roundtrip_transcript_and_source():
    t = TranscriptResult(
        segments=[_segment()],
        speaker_split_failed=False,
        failure=FailureCode.TIMEOUT,
        stt_model="m",
        diarize_model="d",
        audio_seconds=3.0,
    )
    roundtrip(t)
    roundtrip(
        ReadingSource(
            kind=InputKind.AUDIO,
            segments=[_segment()],
            other_speaker="화자1",
            speaker_split_failed=False,
        )
    )


def test_roundtrip_reading():
    r = ClaimReading.model_validate(
        {
            "identity": [{"kind": "institution", "name": "검찰", "quote": "검찰입니다"}],
            "pretexts": [{"quote": "수사 중"}],
            "demands": [{"kind": "money_transfer", "quote": "옮기세요"}],
            "blockers": [{"quote": "통화 안 돼"}],
            "other_speaker": "화자1",
            "speaker_confidence": "high",
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
def test_reading_limits(field, item, n):
    with pytest.raises(ValidationError):
        ClaimReading.model_validate({field: [item] * n})


def test_reading_limits_boundary_ok():
    ClaimReading.model_validate({"demands": [{"kind": "other", "quote": "q"}] * 6})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Segment.model_validate({"side": "other", "text": "x", "bogus": 1})
