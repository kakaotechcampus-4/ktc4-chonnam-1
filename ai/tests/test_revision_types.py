import pytest
from pydantic import ValidationError

from ai.types import (
    AnalysisResponse,
    AnalysisStatus,
    Brand,
    EnvironmentDetails,
    EnvironmentPart,
    EnvDoubt,
    FailureCode,
    IsolatedPage,
    MessageDetails,
    MessageDoubt,
    MessagePart,
    PageAnalysis,
    PageProposal,
    SignalAnalysis,
    Topic,
    UrlAnalysis,
)


def valid_message_part(**overrides):
    data = {
        "brand": Brand.CJ_LOGISTICS,
        "category": Topic.PARCEL,
        "answer": False,
        "details": {"doubt": MessageDoubt.PARCEL_LOOKUP, "reason": "배송 조회"},
    }
    data.update(overrides)
    return MessagePart(**data)


def valid_environment_part(**overrides):
    data = {
        "brand": Brand.CJ_LOGISTICS,
        "category": Topic.PARCEL,
        "answer": False,
        "details": {"doubt": EnvDoubt.PARCEL_WIDGET, "reason": "조회 입력란"},
    }
    data.update(overrides)
    return EnvironmentPart(**data)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_official_requires_real_boolean(value):
    with pytest.raises(ValidationError):
        UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=value)


def test_doubt_enums_are_not_interchangeable():
    with pytest.raises(ValidationError):
        MessageDetails(doubt="로그인·인증 입력폼", reason="폼")
    with pytest.raises(ValidationError):
        EnvironmentDetails(doubt="앱 설치", reason="설치 안내")


@pytest.mark.parametrize("field", ["final_url", "domain"])
def test_url_analysis_rejects_blank_strings_without_mutating_normal_input(field):
    values = {"final_url": " https://example.com/a ", "domain": " example.com ", "official": True}
    values[field] = " \t "
    with pytest.raises(ValidationError):
        UrlAnalysis(**values)

    url = UrlAnalysis(final_url=" https://example.com/a ", domain=" example.com ", official=True)
    assert url.final_url == " https://example.com/a "
    assert url.domain == " example.com "


def test_url_analysis_ignores_extra_inbound_keys_and_requires_official():
    url = UrlAnalysis.model_validate(
        {"final_url": "https://example.com/a", "domain": "example.com", "official": False, "score": 3}
    )
    assert url.official is False
    assert not hasattr(url, "score")
    with pytest.raises(ValidationError):
        UrlAnalysis(final_url="https://example.com/a", domain="example.com")


def test_enums_reject_integer_values():
    with pytest.raises(ValidationError):
        MessageDetails(doubt=1)
    with pytest.raises(ValidationError):
        EnvironmentDetails(doubt=1)


def test_analysis_response_requires_all_null_parts_for_official_url():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=True)
    response = AnalysisResponse(url=url, message=MessagePart(), env=EnvironmentPart(), result=True)
    assert response.model_dump(mode="json")["message"] == {
        "brand": None, "category": None, "answer": None, "details": {"doubt": None, "reason": None}
    }
    with pytest.raises(ValidationError):
        AnalysisResponse(url=url, message=valid_message_part(), env=EnvironmentPart(), result=True)


def test_analysis_response_rejects_mixed_null_parts_and_null_false_result_parts():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=False)
    with pytest.raises(ValidationError):
        AnalysisResponse(url=url, message=valid_message_part(answer=None), env=valid_environment_part(), result=False)
    with pytest.raises(ValidationError):
        AnalysisResponse(url=url, message=MessagePart(), env=valid_environment_part(), result=False)


def test_analysis_response_requires_result_to_match_official():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=False)
    with pytest.raises(ValidationError):
        AnalysisResponse(url=url, message=valid_message_part(), env=valid_environment_part(), result=True)


@pytest.mark.parametrize("model", [SignalAnalysis, PageAnalysis])
def test_analysis_states_require_matching_failure(model):
    with pytest.raises(ValidationError):
        model(status=AnalysisStatus.COMPLETED, failure=FailureCode.TIMEOUT)
    with pytest.raises(ValidationError):
        model(status=AnalysisStatus.FALLBACK)
    assert model(status=AnalysisStatus.COMPLETED).failure is None
    assert model(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT).failure is FailureCode.TIMEOUT


def test_page_proposal_and_isolated_page_contracts_are_strict():
    assert PageProposal().model_dump(mode="json") == {"brand": {"value": None, "evidence": None}, "category": {"value": None, "evidence": None}, "signals": []}
    assert IsolatedPage(brand="", category="", info="").info == ""
    with pytest.raises(ValidationError):
        PageProposal.model_validate({"answer": False})


def test_analysis_response_round_trips_as_json():
    response = AnalysisResponse(
        url=UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=False),
        message=valid_message_part(),
        env=valid_environment_part(),
        result=False,
    )
    assert AnalysisResponse.model_validate(response.model_dump(mode="json")) == response
