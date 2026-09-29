"""주장·행동 대조는 차이만 보고하고, 확인하지 못한 값을 차이로 세지 않는다."""

import pytest

from ai.pipeline import compare as module
from ai.pipeline.compare import Conflict, compare_claims
from ai.types import (
    AnswerState, Brand, EnvDoubt, EnvironmentDetails, EnvironmentPart,
    FailureCode, MessageDetails, MessageDoubt, MessagePart, Reason, Topic,
)


def message(brand=Brand.HANJIN, category=Topic.PARCEL, doubt=MessageDoubt.PARCEL_LOOKUP):
    doubts = [] if doubt is None else [{"value": doubt, "evidence": "문자 근거"}]
    return MessagePart(brand=brand, category=category, answer=AnswerState.NO_RISK_FOUND,
        details=MessageDetails(doubts=doubts, reason=Reason(text="문자 근거")))


def env(brand=Brand.HANJIN, category=Topic.PARCEL, doubt=EnvDoubt.PARCEL_WIDGET):
    doubts = [] if doubt is None else [{"value": doubt, "evidence": "페이지 근거"}]
    return EnvironmentPart(brand=brand, category=category, answer=AnswerState.NO_RISK_FOUND,
        details=EnvironmentDetails(doubts=doubts, reason=Reason(text="페이지 근거")))


def not_run(model, details):
    return model(answer=AnswerState.NOT_RUN, details=details(
        reason=Reason(text="없음", failures=[FailureCode.MISSING_RESULT])))


def test_matching_claims_report_no_conflict():
    assert compare_claims(message(), env()) == ()


def test_different_company_is_a_brand_conflict():
    conflicts = compare_claims(message(brand=Brand.HANJIN), env(brand=Brand.LOGEN))
    assert Conflict.BRAND in conflicts


def test_same_family_service_is_not_a_brand_conflict():
    conflicts = compare_claims(
        message(brand=Brand.CJ_LOGISTICS), env(brand=Brand.CJ_PARCEL))
    assert Conflict.BRAND not in conflicts


def test_different_topic_is_a_topic_conflict():
    conflicts = compare_claims(
        message(category=Topic.PARCEL), env(category=Topic.FINANCE))
    assert Conflict.TOPIC in conflicts


def test_parcel_lookup_against_login_form_is_a_doubt_conflict():
    conflicts = compare_claims(
        message(doubt=MessageDoubt.PARCEL_LOOKUP), env(doubt=EnvDoubt.LOGIN_FORM))
    assert Conflict.DOUBT in conflicts


def test_address_edit_against_address_form_is_consistent():
    conflicts = compare_claims(
        message(doubt=MessageDoubt.ADDRESS_EDIT), env(doubt=EnvDoubt.ADDRESS_FORM))
    assert Conflict.DOUBT not in conflicts


@pytest.mark.parametrize("unknown_brand", [Brand.UNKNOWN])
def test_unknown_brand_is_not_a_conflict(unknown_brand):
    assert compare_claims(message(brand=unknown_brand), env(brand=Brand.LOGEN)) == ()
    assert compare_claims(message(brand=Brand.LOGEN), env(brand=unknown_brand)) == ()


def test_unknown_topic_is_not_a_conflict():
    assert compare_claims(
        message(category=Topic.UNKNOWN), env(category=Topic.FINANCE)) == ()


@pytest.mark.parametrize("absent", [None, EnvDoubt.UNKNOWN])
def test_absent_page_element_is_not_a_doubt_conflict(absent):
    conflicts = compare_claims(
        message(doubt=MessageDoubt.PARCEL_LOOKUP), env(doubt=absent))
    assert Conflict.DOUBT not in conflicts


@pytest.mark.parametrize("absent", [None, MessageDoubt.UNKNOWN])
def test_absent_message_purpose_is_not_a_doubt_conflict(absent):
    conflicts = compare_claims(message(doubt=absent), env(doubt=EnvDoubt.LOGIN_FORM))
    assert Conflict.DOUBT not in conflicts


@pytest.mark.parametrize("vague", sorted(module._NOT_COMPARABLE, key=lambda item: item.value))
def test_unspecified_purpose_is_not_compared(vague):
    conflicts = compare_claims(message(doubt=vague), env(doubt=EnvDoubt.LOGIN_FORM))
    assert Conflict.DOUBT not in conflicts


def test_early_return_parts_produce_no_conflict():
    assert compare_claims(not_run(MessagePart, MessageDetails), not_run(EnvironmentPart, EnvironmentDetails)) == ()


def test_every_difference_is_reported_together():
    conflicts = compare_claims(
        message(brand=Brand.HANJIN, category=Topic.PARCEL, doubt=MessageDoubt.PARCEL_LOOKUP),
        env(brand=Brand.KISA, category=Topic.FINANCE, doubt=EnvDoubt.LOGIN_FORM))
    assert set(conflicts) == {Conflict.BRAND, Conflict.TOPIC, Conflict.DOUBT}


def test_comparison_does_not_mutate_either_part():
    left, right = message(), env(brand=Brand.LOGEN)
    before = left.model_dump(), right.model_dump()
    compare_claims(left, right)
    assert (left.model_dump(), right.model_dump()) == before


def test_every_message_purpose_has_a_comparison_rule():
    """새 MessageDoubt 를 표에 넣지 않으면 조용히 전부 불일치가 된다."""
    covered = set(module._CONSISTENT_ELEMENTS) | module._NOT_COMPARABLE
    covered |= {MessageDoubt.UNKNOWN}
    assert covered == set(MessageDoubt)
