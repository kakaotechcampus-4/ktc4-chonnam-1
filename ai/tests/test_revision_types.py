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


def test_revisions_enums_match_the_closed_contract():
    assert {item.value for item in Brand} == {
        "CJ대한통운", "CJ택배", "CJ익스프레스", "CJ오쇼핑", "한진택배", "로젠택배",
        "우체국택배", "DHL", "현대택배", "롯데택배", "CU", "대신택배", "KGB택배",
        "경동택배", "합동택배", "쿠팡", "옥션", "롯데몰", "카카오톡 선물하기", "7-11",
        "라쿠텐 익스프레스", "KISA", "검찰청", "unknown",
    }
    assert {item.value for item in Topic} == {
        "택배", "쇼핑", "금융", "공공기관", "의료·건강", "보안", "선물·이벤트", "unknown",
    }
    assert {item.value for item in MessageDoubt} == {
        "앱 설치", "주소 입력·수정", "주소 확인", "본인 확인", "정보 입력", "사진 확인",
        "배송 조회", "상세 내용 확인", "주문 취소·환불", "수령·일정 확인", "금전 인출",
        "전화 응대", "링크 접속", "없음", "unknown",
    }
    assert {item.value for item in EnvDoubt} == {
        "앱 다운로드 링크", "로그인·인증 입력폼", "결제 요청 요소", "개인정보 입력폼", "주소 입력폼",
        "배송 조회 요소", "사진·문서 열람 요소", "없음", "unknown",
    }


def test_analysis_response_accepts_populated_parts_for_official_url():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=True)
    response = AnalysisResponse(url=url, message=valid_message_part(answer=True),
        env=valid_environment_part(answer=True), result=True)
    assert response.result is True


@pytest.mark.parametrize("factory", [valid_message_part, valid_environment_part])
def test_completed_parts_reject_missing_leaf_values(factory):
    with pytest.raises(ValidationError):
        factory(details={"doubt": None, "reason": "분석 결과"})


@pytest.mark.parametrize("factory", [valid_message_part, valid_environment_part])
@pytest.mark.parametrize("reason", [None, "", " \t "])
def test_incomplete_nonempty_parts_require_nonblank_reason(factory, reason):
    with pytest.raises(ValidationError):
        factory(answer=None, details={"doubt": None, "reason": reason})


def test_analysis_response_rejects_empty_parts_on_direct_deserialization():
    data = {
        "url": {"final_url": "https://example.com/a", "domain": "example.com", "official": True},
        "message": MessagePart().model_dump(mode="json"),
        "env": EnvironmentPart().model_dump(mode="json"),
        "result": False,
    }
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate(data)
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate_json(__import__("json").dumps(data))


def test_analysis_response_requires_aggregate_result():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=True)
    response = AnalysisResponse(url=url, message=valid_message_part(answer=True),
        env=valid_environment_part(answer=True), result=True)
    with pytest.raises(ValidationError):
        type(response).model_validate({**response.model_dump(), "result": not response.result})


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
