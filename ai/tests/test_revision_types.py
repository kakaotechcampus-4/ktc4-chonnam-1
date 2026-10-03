import json

import pytest
from pydantic import ValidationError

from ai.types import (
    AnalysisResponse,
    AnalysisStatus,
    AnswerState,
    Brand,
    DomainMatch,
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


SIGNAL = {"code": "credential_request", "evidence": "비밀번호를 입력해주세요"}


def reason(*failures):
    return {"text": "분석 근거", "failures": list(failures)}


def valid_message_part(**overrides):
    data = {
        "brand": Brand.CJ_LOGISTICS,
        "category": Topic.PARCEL,
        "answer": AnswerState.NO_RISK_FOUND,
        "details": {
            "doubts": [{"value": MessageDoubt.PARCEL_LOOKUP, "evidence": "배송 조회"}],
            "reason": reason(),
        },
    }
    data.update(overrides)
    return MessagePart(**data)


def valid_environment_part(**overrides):
    data = {
        "brand": Brand.CJ_LOGISTICS,
        "category": Topic.PARCEL,
        "answer": AnswerState.NO_RISK_FOUND,
        "details": {
            "doubts": [{"value": EnvDoubt.PARCEL_WIDGET, "evidence": "조회 입력란"}],
            "reason": reason(),
        },
    }
    data.update(overrides)
    return EnvironmentPart(**data)


def url(official=DomainMatch.OFFICIAL, **extra):
    return UrlAnalysis(final_url="https://example.com/a", domain="example.com",
        official=official, **extra)


@pytest.mark.parametrize("value", [True, False, None, "no_url", "yes"])
def test_official_requires_wire_domain_match(value):
    with pytest.raises(ValidationError):
        url(official=value)


def test_doubt_enums_are_not_interchangeable():
    with pytest.raises(ValidationError):
        MessageDetails(doubts=[{"value": "로그인·인증 입력폼", "evidence": "폼"}], reason=reason())
    with pytest.raises(ValidationError):
        EnvironmentDetails(doubts=[{"value": "앱 설치", "evidence": "설치"}], reason=reason())


@pytest.mark.parametrize("field", ["final_url", "domain"])
def test_url_analysis_rejects_blank_strings_without_mutating_normal_input(field):
    values = {"final_url": " https://example.com/a ", "domain": " example.com ", "official": "official"}
    values[field] = " \t "
    with pytest.raises(ValidationError):
        UrlAnalysis(**values)

    parsed = UrlAnalysis(final_url=" https://example.com/a ", domain=" example.com ", official="official")
    assert parsed.final_url == " https://example.com/a "
    assert parsed.domain == " example.com "


def test_url_analysis_ignores_extra_inbound_keys_and_requires_official():
    parsed = UrlAnalysis.model_validate(
        {"final_url": "https://example.com/a", "domain": "example.com",
         "official": "not_registered", "extra": 3}
    )
    assert parsed.official is DomainMatch.NOT_REGISTERED
    assert not hasattr(parsed, "extra")
    with pytest.raises(ValidationError):
        UrlAnalysis(final_url="https://example.com/a", domain="example.com")


def test_scan_defaults_to_unreceived_and_bounds_score():
    assert url().scan.model_dump() == {"score": None, "scanned_at": None}
    scanned = url(scan={"score": 35, "scanned_at": "2026-09-29T10:00:30Z"})
    assert scanned.scan.score == 35
    assert scanned.model_dump(mode="json")["scan"]["scanned_at"] == "2026-09-29T10:00:30Z"
    for score in (-101, 101):
        with pytest.raises(ValidationError):
            url(scan={"score": score})


def test_enums_reject_integer_values():
    with pytest.raises(ValidationError):
        MessageDetails(doubts=[{"value": 1, "evidence": "x"}], reason=reason())
    with pytest.raises(ValidationError):
        valid_message_part(answer=1)


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
        "전화 응대", "링크 접속", "unknown",
    }
    assert {item.value for item in EnvDoubt} == {
        "앱 다운로드 링크", "로그인·인증 입력폼", "결제 요청 요소", "개인정보 입력폼", "주소 입력폼",
        "배송 조회 요소", "사진·문서 열람 요소", "unknown",
    }
    assert {item.value for item in AnswerState} == {
        "no_risk_found", "risk_found", "partial", "failed", "not_run",
    }


def test_analysis_response_accepts_populated_parts_for_official_url():
    response = AnalysisResponse(url=url(), message=valid_message_part(),
        env=valid_environment_part(), result=True)
    assert response.result is True


@pytest.mark.parametrize("factory", [valid_message_part, valid_environment_part])
@pytest.mark.parametrize(("answer", "signals", "failures"), [
    (AnswerState.RISK_FOUND, [], []),
    (AnswerState.RISK_FOUND, [SIGNAL], ["timeout"]),
    (AnswerState.NO_RISK_FOUND, [SIGNAL], []),
    (AnswerState.NO_RISK_FOUND, [], ["timeout"]),
    (AnswerState.PARTIAL, [], []),
    (AnswerState.FAILED, [SIGNAL], ["collection_failed"]),
    (AnswerState.FAILED, [], []),
    (AnswerState.NOT_RUN, [SIGNAL], ["missing_result"]),
    (AnswerState.NOT_RUN, [], []),
])
def test_answer_rules_reject_inconsistent_signals_and_failures(factory, answer, signals, failures):
    with pytest.raises(ValidationError):
        factory(answer=answer, details={"signals": signals, "reason": reason(*failures)})


@pytest.mark.parametrize("factory", [valid_message_part, valid_environment_part])
@pytest.mark.parametrize(("answer", "signals", "failures"), [
    (AnswerState.RISK_FOUND, [SIGNAL], []),
    (AnswerState.NO_RISK_FOUND, [], []),
    (AnswerState.PARTIAL, [], ["partial_content"]),
    (AnswerState.PARTIAL, [SIGNAL], ["input_too_large"]),
    (AnswerState.FAILED, [], ["collection_failed"]),
    (AnswerState.NOT_RUN, [], ["missing_result"]),
])
def test_answer_rules_accept_documented_cases(factory, answer, signals, failures):
    part = factory(answer=answer, details={"signals": signals, "reason": reason(*failures)})
    assert part.answer is answer


@pytest.mark.parametrize("factory", [valid_message_part, valid_environment_part])
@pytest.mark.parametrize("text", ["", " \t "])
def test_reason_text_must_be_nonblank(factory, text):
    with pytest.raises(ValidationError):
        factory(details={"reason": {"text": text, "failures": []}})


def test_expired_link_is_not_a_wire_signal():
    with pytest.raises(ValidationError):
        valid_message_part(answer=AnswerState.RISK_FOUND, details={
            "signals": [{"code": "expired_link", "evidence": "만료된 링크"}], "reason": reason()})


def test_analysis_response_rejects_parts_without_answer():
    data = {
        "url": {"final_url": "https://example.com/a", "domain": "example.com", "official": "official"},
        "message": {"details": {"reason": reason()}},
        "env": {"details": {"reason": reason()}},
        "result": False,
    }
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate(data)
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(("official", "message", "env", "expected"), [
    (DomainMatch.OFFICIAL, AnswerState.NO_RISK_FOUND, AnswerState.NO_RISK_FOUND, True),
    (DomainMatch.NOT_REGISTERED, AnswerState.NO_RISK_FOUND, AnswerState.NO_RISK_FOUND, False),
    (DomainMatch.OFFICIAL, AnswerState.PARTIAL, AnswerState.NO_RISK_FOUND, False),
    (DomainMatch.OFFICIAL, AnswerState.NO_RISK_FOUND, AnswerState.NOT_RUN, False),
])
def test_analysis_response_requires_aggregate_result(official, message, env, expected):
    failures = lambda answer: [] if answer is AnswerState.NO_RISK_FOUND else ["missing_result"]
    parts = (
        valid_message_part(answer=message, details={"reason": reason(*failures(message))}),
        valid_environment_part(answer=env, details={"reason": reason(*failures(env))}),
    )
    response = AnalysisResponse(url=url(official), message=parts[0], env=parts[1], result=expected)
    with pytest.raises(ValidationError):
        type(response).model_validate({**response.model_dump(), "result": not expected})


def test_url_scan_does_not_affect_result():
    response = AnalysisResponse(url=url(scan={"score": 100}), message=valid_message_part(),
        env=valid_environment_part(), result=True)
    assert response.result is True


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
    assert IsolatedPage(brand="", category="", info="").collected_at is None
    with pytest.raises(ValidationError):
        PageProposal.model_validate({"answer": False})


def test_analysis_response_round_trips_as_json():
    response = AnalysisResponse(
        url=url(DomainMatch.NOT_REGISTERED, scan={"score": 35, "scanned_at": "2026-09-29T10:00:30Z"}),
        message=valid_message_part(answer=AnswerState.RISK_FOUND,
            details={"signals": [SIGNAL], "reason": reason()}),
        env=valid_environment_part(answer=AnswerState.PARTIAL,
            collected_at="2026-09-29T10:00:25Z", details={"reason": reason("input_too_large")}),
        result=False,
    )
    wire = response.model_dump(mode="json")
    assert wire["url"]["official"] == "not_registered"
    assert wire["message"]["details"]["signals"] == [SIGNAL]
    assert AnalysisResponse.model_validate(wire) == response
