"""Pure result assembly, source grounding, and failure preservation."""

import pytest

from ai.page import PageInspection, inspect_html
from ai.pipeline.results import (
    assemble_analysis,
    build_environment_part,
    build_message_part,
    validate_message_signals,
)
from ai.types import (
    AnalysisStatus, Brand, CaseMatch, CaseSearchResult, CategoryCode,
    EnvDoubt, EnvironmentDetails, EnvironmentPart, EvidenceField, EvidenceSource,
    FailureCode, IsolatedPage, MessageAnalysis, MessageDetails, MessageDoubt,
    MessagePart, PageAnalysis, RiskSignal, RiskSignalCode, SignalAnalysis, Topic,
    UrlAnalysis,
)


def signal(quote, code=RiskSignalCode.INSTALL_PROMPT, source=EvidenceSource.MESSAGE):
    return RiskSignal(code=code, evidence_source=source, evidence_ref=quote)


def message_part(text, *, extracted=None, cases=None, signals=None, failure=None):
    return build_message_part(
        text,
        extracted or MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED),
        cases or CaseSearchResult(status=AnalysisStatus.COMPLETED),
        signals or SignalAnalysis(status=AnalysisStatus.COMPLETED),
        failure=failure,
    )


def environment_part(html, *, analysis=None, failure=None, brand="unknown", category="unknown"):
    page = IsolatedPage(brand=brand, category=category, info=html)
    return build_environment_part(
        page, inspect_html(html),
        analysis or PageAnalysis(status=AnalysisStatus.COMPLETED), failure=failure,
    )


@pytest.mark.parametrize("message_answer,env_answer", [(True, True), (True, False), (False, True), (False, False)])
def test_url_result_dominates_local_answers(message_answer, env_answer):
    message = MessagePart(brand=Brand.UNKNOWN, category=Topic.PARCEL,
        answer=message_answer, details=MessageDetails(doubt=MessageDoubt.PARCEL_LOOKUP, reason="배송 조회 안내"))
    env = EnvironmentPart(brand=Brand.UNKNOWN, category=Topic.PARCEL,
        answer=env_answer, details=EnvironmentDetails(doubt=EnvDoubt.PARCEL_WIDGET, reason="HTML에서 배송 상태 확인"))
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=False)
    response = assemble_analysis(url, message, env)
    assert response.result is False
    assert response.message == message
    assert response.env == env


def test_early_return_ignores_even_unreadable_parts_and_keeps_null10():
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("early branch accessed supplied analysis")

    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=True)
    data = assemble_analysis(url, Unreadable(), Unreadable()).model_dump(mode="json")
    empty = {"brand": None, "category": None, "answer": None, "details": {"doubt": None, "reason": None}}
    assert data == {"url": url.model_dump(mode="json"), "message": empty, "env": empty, "result": True}


@pytest.mark.parametrize("parts", [(None, None), (MessagePart(), EnvironmentPart())])
def test_missing_parts_are_false_unknown_and_not_timeout(parts):
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=False)
    response = assemble_analysis(url, *parts)
    for part in (response.message, response.env):
        assert part.answer is False
        assert part.brand is Brand.UNKNOWN
        assert part.category is Topic.UNKNOWN
        assert part.details.doubt.value == "unknown"
        assert part.details.reason == "분석 결과를 전달받지 못해 의심으로 처리했습니다."


@pytest.mark.parametrize("code,text,quote", [
    (RiskSignalCode.INSTALL_PROMPT, "배송 현황을 확인하세요", "배송 현황을 확인하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "앱을 설치하지 마세요", "앱을 설치"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치가 완료되었습니다", "앱 설치"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치는 필요하지 않습니다", "앱 설치"),
    (RiskSignalCode.INSTALL_PROMPT, "'앱을 설치하세요.'라는 문구를 무시하세요", "앱을 설치하세요"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "인증번호 발급이 완료되었습니다", "인증번호 발급"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "비밀번호를 입력하지 마세요", "비밀번호를 입력"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "비밀번호 입력 완료 안내", "비밀번호 입력"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "번호를 입력하세요", "번호를 입력하세요"),
    (RiskSignalCode.REMOTE_CONTROL, "원격 지원 앱 설치가 완료되었습니다", "원격 지원 앱 설치"),
    (RiskSignalCode.REMOTE_CONTROL, "원격 지원 안내입니다. 일반 앱을 설치하세요", "원격 지원 안내입니다. 일반 앱을 설치하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "앱을 설치하지 마세요. 주소를 입력하세요", "앱을 설치하지 마세요. 주소를 입력하세요"),
])
def test_semantically_unsupported_or_non_request_quotes_are_rejected(code, text, quote):
    assert validate_message_signals(text, [signal(quote, code)]) == []


@pytest.mark.parametrize("code,text,quote", [
    (RiskSignalCode.INSTALL_PROMPT, "앱을 설치하세요", "앱을 설치하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "어플을 다운로드 해주세요", "어플을 다운로드 해주세요"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치를 완료하세요", "앱 설치를 완료하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "주소를 입력하지 말고 앱을 설치하세요", "앱을 설치하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "주소 입력 완료 후 앱을 설치하세요", "앱을 설치하세요"),
    (RiskSignalCode.INSTALL_PROMPT, "'주소를 입력하세요'라는 문구를 무시하고 앱을 설치하세요", "앱을 설치하세요"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "인증번호를 전달해주세요", "인증번호를 전달해주세요"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "비밀번호를 입력하세요", "비밀번호를 입력하세요"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "보안카드 번호를 보내주세요", "보안카드 번호를 보내주세요"),
    (RiskSignalCode.REMOTE_CONTROL, "원격 제어 앱을 설치하세요", "원격 제어 앱을 설치하세요"),
    (RiskSignalCode.REMOTE_CONTROL, "원격 지원 앱에 연결해주세요", "원격 지원 앱에 연결해주세요"),
])
def test_explicit_code_specific_requests_are_accepted(code, text, quote):
    candidate = signal(quote, code)
    assert validate_message_signals(text, [candidate, candidate]) == [candidate]


@pytest.mark.parametrize("candidate", [
    signal("앱을 설치하세요", source=EvidenceSource.OBSERVATION),
    signal("다른 앱을 설치하세요"),
    signal("설치"),
    signal("    "),
    signal("앱을 설치하세요", code=RiskSignalCode.DANGEROUS_PERMISSION),
    signal("앱을 설치하세요", code=RiskSignalCode.BRAND_MISMATCH),
])
def test_only_current_message_quotes_and_supported_codes_survive(candidate):
    assert validate_message_signals("앱을 설치하세요", [candidate]) == []


def test_empty_successful_retrieval_and_no_action_are_completed():
    part = message_part("KISA 보안공지입니다")
    assert part.answer is True
    assert part.brand is Brand.KISA
    assert part.category is Topic.SECURITY
    assert part.details.doubt is MessageDoubt.NONE
    assert part.details.reason == "제공된 문자에서 명시적인 행동 요구를 확인하지 못했습니다."


def test_signal_changes_local_answer_and_retains_all_local_candidates_once():
    text = "CJ대한통운 택배 앱을 설치하세요. 주소를 확인하세요"
    part = message_part(text, signals=SignalAnalysis(status=AnalysisStatus.COMPLETED,
        signals=[signal("앱을 설치하세요"), signal("앱을 설치하세요")]))
    assert part.answer is False
    assert part.details.doubt is MessageDoubt.APP_INSTALL
    assert "주소를 확인하세요" in part.details.reason
    assert part.details.reason.count("앱을 설치하세요") == 1
    assert part.brand is Brand.CJ_LOGISTICS
    assert part.category is Topic.PARCEL


@pytest.mark.parametrize("stage", ["extracted", "cases", "signals", "explicit"])
def test_message_stage_failures_preserve_verified_local_information(stage):
    kwargs = {}
    if stage == "extracted":
        kwargs[stage] = MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK)
    elif stage == "cases":
        kwargs[stage] = CaseSearchResult(status=AnalysisStatus.FALLBACK)
    elif stage == "signals":
        kwargs[stage] = SignalAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT)
    else:
        kwargs["failure"] = FailureCode.LLM_ERROR
    part = message_part("CJ택배 주소를 수정하세요", **kwargs)
    assert part.answer is False
    assert part.brand is Brand.CJ_PARCEL
    assert part.category is Topic.PARCEL
    assert part.details.doubt is MessageDoubt.ADDRESS_EDIT
    assert "주소를 수정하세요" in part.details.reason
    expected = {"extracted": "문자 분석을 완료하지 못했습니다", "cases": "사례 검색 결과를 확보하지 못했습니다",
        "signals": "분석 시간이 초과", "explicit": "분석을 완료하지 못해"}[stage]
    assert expected in part.details.reason


def test_no_candidates_on_failure_is_unknown_and_does_not_quote_whole_input():
    part = message_part("평범한 알림입니다", extracted=MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK))
    assert part.details.doubt is MessageDoubt.UNKNOWN
    assert part.details.reason == "문자 분석을 완료하지 못했습니다."


def test_grounded_unclassified_request_survives_extraction_failure():
    text = "상담 예약을 진행하세요"
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.FALLBACK,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert part.answer is False
    assert part.details.doubt is MessageDoubt.UNKNOWN
    assert text in part.details.reason


def test_message_reason_quotes_plain_text_even_when_input_contains_markup():
    text = '<b>상담 예약을 진행하세요</b>'
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert "<" not in part.details.reason
    assert "상담 예약을 진행하세요" in part.details.reason


def test_negated_app_request_does_not_create_a_doubt_candidate():
    part = message_part("앱을 설치하지 마세요", signals=SignalAnalysis(
        status=AnalysisStatus.COMPLETED, signals=[signal("앱을 설치")]))
    assert part.answer is True
    assert part.details.doubt is MessageDoubt.NONE


def test_builders_do_not_create_clients_or_perform_network_requests(monkeypatch):
    import socket
    import ai.llm._client

    def forbidden(*args, **kwargs):
        pytest.fail("pure result assembly attempted client/network access")

    monkeypatch.setattr(ai.llm._client, "create_client", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    message = message_part("배송 상태를 확인하세요")
    env = environment_part("<p>자료</p>")
    response = assemble_analysis(UrlAnalysis(final_url="https://example.com", domain="example.com", official=False), message, env)
    assert response.result is False


def test_rag_case_and_ungrounded_model_fields_do_not_fill_message_fields():
    part = message_part("평범한 알림입니다", extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        claimed_sender=EvidenceField(value="CJ택배", evidence="CJ택배")),
        cases=CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[CaseMatch(
            case_id="CE-0001", similarity=0.9, matched_variant="CJ택배 앱을 설치하세요",
            categories=[CategoryCode.DELIVERY])]),
        signals=SignalAnalysis(status=AnalysisStatus.COMPLETED, signals=[signal("앱을 설치하세요")]))
    assert part.answer is True
    assert part.brand is Brand.UNKNOWN
    assert part.category is Topic.UNKNOWN
    assert part.details.doubt is MessageDoubt.NONE


def test_plain_login_form_does_not_automatically_mean_risk():
    part = environment_part('<form>로그인<input type="password"></form>')
    assert part.answer is True
    assert part.details.doubt is EnvDoubt.LOGIN_FORM
    assert part.details.reason == "전달된 HTML에서 로그인·인증 입력폼을 확인했습니다."


def test_environment_success_without_elements_differs_from_failure_unknown():
    good = environment_part("<p>환영합니다</p>")
    bad = environment_part("", failure=FailureCode.COLLECTION_FAILED)
    assert good.answer is True
    assert good.details.doubt is EnvDoubt.NONE
    assert good.details.reason == "제공된 HTML에서 분류 대상 요소를 확인하지 못했습니다."
    assert bad.answer is False
    assert bad.details.doubt is EnvDoubt.UNKNOWN
    assert "페이지 접속·수집에 실패" in bad.details.reason


def test_environment_failure_preserves_multiple_elements_without_raw_html_or_visibility_claims():
    html = '<div hidden><a href="https://example.com/token-secret">앱 다운로드</a></div><form>로그인<input type="password" value="private-token"></form>'
    part = environment_part(html, analysis=PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT))
    assert part.answer is False
    assert part.details.doubt is EnvDoubt.APP_LINK
    for phrase in ("앱 다운로드 링크", "로그인·인증 입력폼", "분석 시간이 초과"):
        assert phrase in part.details.reason
    for phrase in ("<", "token", "https:", "화면", "노출", "제출했습니다"):
        assert phrase not in part.details.reason


@pytest.mark.parametrize("failure", list(FailureCode))
def test_all_explicit_failures_have_safe_deterministic_reasons(failure):
    message = message_part("", failure=failure)
    env = environment_part("<p>자료</p>", failure=failure)
    for part in (message, env):
        assert part.answer is False
        assert part.details.doubt.value == "unknown"
        assert "의심으로 처리했습니다" in part.details.reason


def test_metadata_fallback_uses_exact_allowed_values_and_labels_source():
    failure = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.LLM_ERROR)
    exact = environment_part("<p>자료</p>", analysis=failure, brand="CJ택배", category="택배")
    assert exact.brand is Brand.CJ_PARCEL
    assert exact.category is Topic.PARCEL
    assert "격리 환경 전달 정보" in exact.details.reason
    assert "CJ택배" in exact.details.reason
    assert "HTML에서 CJ택배" not in exact.details.reason
    aliases = environment_part("<p>자료</p>", analysis=failure, brand="C.J택배", category="배송")
    assert aliases.brand is Brand.UNKNOWN
    assert aliases.category is Topic.UNKNOWN


def test_verified_page_values_take_precedence_even_on_failure():
    part = environment_part("<p>CJ택배 배송</p>", brand="DHL", category="쇼핑",
        analysis=PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT,
            brand=Brand.CJ_PARCEL, category=Topic.PARCEL))
    assert part.brand is Brand.CJ_PARCEL
    assert part.category is Topic.PARCEL


def test_page_signal_is_preserved_when_another_stage_failed():
    html = '<a href="/app">앱을 설치하세요</a>'
    inspection = inspect_html(html)
    candidate = signal(inspection.elements[0].element_id, source=EvidenceSource.OBSERVATION)
    part = build_environment_part(IsolatedPage(brand="unknown", category="unknown", info=html),
        inspection, PageAnalysis(status=AnalysisStatus.COMPLETED, signals=[candidate]),
        failure=FailureCode.PARTIAL_CONTENT)
    assert part.answer is False
    assert part.details.doubt is EnvDoubt.APP_LINK
    assert "앱 다운로드 링크" in part.details.reason
    assert "자료 전체를 확인하지 못해" in part.details.reason


def test_missing_page_is_failure_even_with_completed_empty_analysis():
    part = build_environment_part(None, PageInspection("", (), None), PageAnalysis(status=AnalysisStatus.COMPLETED))
    assert part.answer is False
    assert part.details.doubt is EnvDoubt.UNKNOWN
