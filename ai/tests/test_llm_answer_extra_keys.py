"""LLM 답 타입은 스키마에 없는 최상위 칸만 버린다 (#145).

게이트웨이를 거치면 strict 스키마가 강제되지 않아 모델이 칸을 지어내 붙인다.
문자 읽기(ExtractedMessage)는 test_analyze.py 에서 SDK 경로로 본다.
"""

import pytest
from pydantic import ValidationError

from ai.case.types import ClaimReading, ClaimReadingResult
from ai.types import (
    EvidenceField,
    PageProposal,
    SignalProposal,
    drop_undeclared_keys,
)


@pytest.mark.parametrize("model, payload", [
    (ClaimReading, {"identity": [], "made_up": "검찰"}),
    (SignalProposal, {"signals": [], "made_up": "검찰"}),
    (PageProposal, {"brand": {"value": None, "evidence": None}, "made_up": "검찰"}),
])
def test_undeclared_top_level_field_is_dropped_and_logged(model, payload, caplog):
    with caplog.at_level("WARNING", logger="ai.types"):
        answer = model.model_validate(payload)

    assert "made_up" not in answer.model_dump()
    assert f"llm answer repaired: model={model.__name__} undeclared=made_up" in caplog.text
    assert "검찰" not in caplog.text


def test_declared_fields_survive_next_to_a_made_up_one():
    reading = ClaimReading.model_validate({
        "demands": [{"kind": "money_transfer", "quote": "입금해 주세요"}],
        "spoken_hosts": ["example.com"],
        "reading_note": "x",
    })
    assert [d.quote for d in reading.demands] == ["입금해 주세요"]
    assert reading.spoken_hosts == ["example.com"]


def test_clean_answer_is_not_reported(caplog):
    with caplog.at_level("WARNING", logger="ai.types"):
        PageProposal.model_validate({"brand": {"value": None, "evidence": None}})
    assert "repaired" not in caplog.text


@pytest.mark.parametrize("model, payload", [
    (PageProposal, {"brand": {"value": "우체국", "evidence": "우체국", "note": "x"}}),
    (ClaimReading, {"demands": [{"kind": "money_transfer", "quote": "입금", "note": "x"}]}),
])
def test_nested_undeclared_field_is_still_rejected(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_wrapping_result_type_stays_strict():
    """읽기를 담는 결과 타입은 LLM 답이 아니다. 모르는 칸을 그대로 거부한다."""
    with pytest.raises(ValidationError):
        ClaimReadingResult.model_validate({"status": "ok", "reading": {}, "made_up": 1})
    with pytest.raises(ValidationError):
        EvidenceField.model_validate({"value": None, "evidence": None, "made_up": 1})


def test_logged_names_are_capped():
    data = {f"k{i}": i for i in range(8)}
    kept, names = drop_undeclared_keys(SignalProposal, {"signals": [], **data})
    assert kept == {"signals": []}
    assert names == ["k0", "k1", "k2", "k3", "k4", "+3"]


def test_non_dict_input_is_left_to_normal_validation():
    assert drop_undeclared_keys(SignalProposal, ["x"]) == (["x"], [])
    with pytest.raises(ValidationError):
        SignalProposal.model_validate(["x"])
