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
    UrlAnalysis, Reason,
)
from ai.types import AnswerState as _Answer, DomainMatch as _Domain

INCOMPLETE = {_Answer.PARTIAL, _Answer.FAILED, _Answer.NOT_RUN}


def first_doubt(part):
    return part.details.doubts[0].value if part.details.doubts else None


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


@pytest.mark.parametrize("text", ["배송 조회를 하지 마세요", "링크를 클릭하지 마세요"])
def test_builder_does_not_describe_a_prohibition_as_a_positive_request(text):
    result = message_part(text)
    assert first_doubt(result) is None
    assert result.answer is _Answer.NO_RISK_FOUND
    assert "라고 안내했습니다" not in result.details.reason.text


def environment_part(html, *, analysis=None, failure=None, brand="unknown", category="unknown"):
    page = IsolatedPage(brand=brand, category=category, info=html)
    return build_environment_part(
        page, inspect_html(html),
        analysis or PageAnalysis(status=AnalysisStatus.COMPLETED), failure=failure,
    )


ANSWERS = list(_Answer)


def _part(model, details, answer, doubts):
    failures = [] if answer in {_Answer.NO_RISK_FOUND, _Answer.RISK_FOUND} else [FailureCode.TIMEOUT]
    signals = [] if answer in {_Answer.NO_RISK_FOUND, _Answer.FAILED, _Answer.NOT_RUN} else [
        {"code": RiskSignalCode.INSTALL_PROMPT, "evidence": "앱을 설치하세요"}]
    return model(brand=Brand.UNKNOWN, category=Topic.PARCEL, answer=answer,
        details=details(doubts=doubts, signals=signals,
            reason=Reason(text="분석 근거", failures=failures)))


@pytest.mark.parametrize("official", list(_Domain.__members__.values())[:2] + [_Domain.UNRESOLVED])
@pytest.mark.parametrize("message_answer", ANSWERS)
@pytest.mark.parametrize("env_answer", ANSWERS)
def test_aggregate_answers_preserve_parts(official, message_answer, env_answer):
    message = _part(MessagePart, MessageDetails, message_answer,
        [{"value": MessageDoubt.PARCEL_LOOKUP, "evidence": "배송 조회"}])
    env = _part(EnvironmentPart, EnvironmentDetails, env_answer,
        [{"value": EnvDoubt.PARCEL_WIDGET, "evidence": "조회 입력란"}])
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=official)
    response = assemble_analysis(url, message, env)
    assert response.result is (official is _Domain.OFFICIAL
        and message_answer is _Answer.NO_RISK_FOUND and env_answer is _Answer.NO_RISK_FOUND)
    assert response.message == message
    assert response.env == env
    assert type(response).model_validate_json(response.model_dump_json()) == response


def test_missing_parts_mean_not_run():
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=_Domain.OFFICIAL)
    response = assemble_analysis(url)
    assert response.result is False
    for part in (response.message, response.env):
        assert part.answer is _Answer.NOT_RUN
        assert part.brand is None
        assert part.category is None
        assert part.details.doubts == part.details.signals == []
        assert part.details.reason.failures == [FailureCode.MISSING_RESULT]
        assert part.details.reason.text


def test_failed_part_keeps_existing_evidence():
    message = MessagePart(answer=_Answer.PARTIAL, details=MessageDetails(
        doubts=[{"value": MessageDoubt.APP_INSTALL, "evidence": "앱을 설치하세요"}],
        reason=Reason(text="문자에서 앱 설치 요청을 확인했으나 분석 시간이 초과되었습니다.",
            failures=[FailureCode.TIMEOUT]),
    ))
    url = UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=_Domain.OFFICIAL)
    response = assemble_analysis(url, message)
    assert response.message == message
    assert response.result is False


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
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치", "앱 설치"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "비밀번호 입력", "비밀번호 입력"),
    (RiskSignalCode.REMOTE_CONTROL, "원격 지원 앱 연결", "원격 지원 앱 연결"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치를 요청하지 않습니다", "앱 설치"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치 요청이 완료되었습니다", "앱 설치 요청"),
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
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치 부탁드립니다", "앱 설치"),
    (RiskSignalCode.CREDENTIAL_REQUEST, "비밀번호 입력 부탁드립니다", "비밀번호 입력"),
    (RiskSignalCode.INSTALL_PROMPT, "앱을 다운로드 받으세요", "앱을 다운로드"),
    (RiskSignalCode.INSTALL_PROMPT, "앱 설치를 요청드립니다", "앱 설치"),
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
    assert part.answer is _Answer.NO_RISK_FOUND
    assert part.brand is Brand.KISA
    assert part.category is Topic.SECURITY
    assert first_doubt(part) is None
    assert part.details.reason.text == "제공된 문자에서 명시적인 행동 요구를 확인하지 못했습니다."


def test_success_unknown_is_not_failure_null():
    part = message_part("안녕하세요")
    assert part.answer is _Answer.NO_RISK_FOUND
    assert part.brand is Brand.UNKNOWN
    assert part.category is Topic.UNKNOWN
    assert first_doubt(part) is None


def test_signal_changes_local_answer_and_retains_all_local_candidates_once():
    text = "CJ대한통운 택배 앱을 설치하세요. 주소를 확인하세요"
    part = message_part(text, signals=SignalAnalysis(status=AnalysisStatus.COMPLETED,
        signals=[signal("앱을 설치하세요"), signal("앱을 설치하세요")]))
    assert part.answer is _Answer.RISK_FOUND
    assert first_doubt(part) is MessageDoubt.APP_INSTALL
    assert "주소를 확인하세요" in part.details.reason.text
    assert part.details.reason.text.count("앱을 설치하세요") == 1
    assert part.brand is Brand.CJ_LOGISTICS
    assert part.category is Topic.PARCEL


def test_message_wire_lists_every_doubt_and_signal_in_source_order():
    text = "앱을 설치하세요. 주소를 확인하세요"
    part = message_part(text, signals=SignalAnalysis(status=AnalysisStatus.COMPLETED,
        signals=[signal("앱을 설치하세요")]))
    assert [item.value for item in part.details.doubts] == [
        MessageDoubt.APP_INSTALL, MessageDoubt.ADDRESS_CHECK]
    assert all(item.evidence in text for item in part.details.doubts)
    assert [(item.code, item.evidence) for item in part.details.signals] == [
        (RiskSignalCode.INSTALL_PROMPT, "앱을 설치하세요")]
    assert part.details.reason.failures == []


def test_environment_wire_signal_describes_element_instead_of_markup():
    html = '<form><label>계좌 비밀번호를 입력하세요<input type="password"></label></form>'
    element = inspect_html(html).elements[0]
    part = environment_part(html, analysis=PageAnalysis(status=AnalysisStatus.COMPLETED,
        signals=[signal(element.element_id, RiskSignalCode.CREDENTIAL_REQUEST,
            EvidenceSource.OBSERVATION)]))
    assert part.answer is _Answer.RISK_FOUND
    assert [item.code for item in part.details.signals] == [RiskSignalCode.CREDENTIAL_REQUEST]
    assert all("<" not in item.evidence for item in part.details.signals + part.details.doubts)
    assert part.details.signals[0].evidence.endswith("입력 필드")


def test_page_absence_is_not_run_unless_collection_failure_is_reported():
    missing = PageInspection(text="", elements=(), failure=FailureCode.MISSING_RESULT)
    fallback = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.MISSING_RESULT)
    not_run = build_environment_part(None, missing, fallback)
    failed = build_environment_part(None, missing, fallback, failure=FailureCode.COLLECTION_FAILED)
    assert (not_run.answer, not_run.details.reason.failures) == (
        _Answer.NOT_RUN, [FailureCode.MISSING_RESULT])
    assert (failed.answer, failed.details.reason.failures) == (
        _Answer.FAILED, [FailureCode.COLLECTION_FAILED])


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
    assert part.answer in INCOMPLETE
    assert part.brand is Brand.CJ_PARCEL
    assert part.category is Topic.PARCEL
    assert first_doubt(part) is MessageDoubt.ADDRESS_EDIT
    assert "주소를 수정하세요" in part.details.reason.text
    expected = {"extracted": "문자 분석을 완료하지 못했습니다", "cases": "사례 검색 결과를 확보하지 못했습니다",
        "signals": "분석 시간이 초과", "explicit": "분석 도중 오류"}[stage]
    assert expected in part.details.reason.text


def test_no_candidates_on_failure_is_unknown_and_does_not_quote_whole_input():
    part = message_part("평범한 알림입니다", extracted=MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK))
    assert part.answer in INCOMPLETE
    assert part.brand is None
    assert part.category is None
    assert first_doubt(part) is None
    assert part.details.reason.text.startswith("문자 분석을 완료하지 못했습니다.")
    assert part.details.reason.failures == [FailureCode.MISSING_RESULT]


def test_grounded_unclassified_request_survives_extraction_failure():
    text = "상담 예약을 진행하세요"
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.FALLBACK,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert part.answer in INCOMPLETE
    assert first_doubt(part) is MessageDoubt.UNKNOWN
    assert text in part.details.reason.text


def test_message_reason_quotes_plain_text_even_when_input_contains_markup():
    text = '<b>상담 예약을 진행하세요</b>'
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert "<" not in part.details.reason.text
    assert "상담 예약을 진행하세요" in part.details.reason.text


@pytest.mark.parametrize("text", [
    "<img src=x 상담 예약을 진행하세요",
    "&lt;img src=x 상담 예약을 진행하세요",
    "&lt;b&gt;상담 예약을 진행하세요&lt;/b&gt;",
    "&amp;lt;img src=x 상담 예약을 진행하세요",
])
def test_message_reason_keeps_malformed_or_encoded_markup_inert(text):
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert "<" not in part.details.reason.text
    assert ">" not in part.details.reason.text
    assert "상담 예약을 진행하세요" in part.details.reason.text
    assert part.details.reason.text.startswith("문자에서 '")


def test_message_reason_preserves_ordinary_ampersand_in_actual_quote():
    text = "A&B 상담 예약을 진행하세요"
    part = message_part(text, extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        requested_actions=[EvidenceField(value="예약 진행", evidence=text)],
    ))
    assert part.details.reason.text == f"문자에서 '{text}'라고 안내했습니다."


def test_negated_app_request_does_not_create_a_doubt_candidate():
    part = message_part("앱을 설치하지 마세요", signals=SignalAnalysis(
        status=AnalysisStatus.COMPLETED, signals=[signal("앱을 설치")]))
    assert part.answer is _Answer.NO_RISK_FOUND
    assert first_doubt(part) is None


def test_builders_do_not_create_clients_or_perform_network_requests(monkeypatch):
    import socket
    import ai.llm._client

    def forbidden(*args, **kwargs):
        pytest.fail("pure result assembly attempted client/network access")

    monkeypatch.setattr(ai.llm._client, "create_client", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    message = message_part("배송 상태를 확인하세요")
    env = environment_part("<p>자료</p>")
    response = assemble_analysis(UrlAnalysis(final_url="https://example.com", domain="example.com", official=_Domain.NOT_REGISTERED), message, env)
    assert response.result is False


def test_rag_case_and_ungrounded_model_fields_do_not_fill_message_fields():
    part = message_part("평범한 알림입니다", extracted=MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        claimed_sender=EvidenceField(value="CJ택배", evidence="CJ택배")),
        cases=CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[CaseMatch(
            case_id="CE-0001", similarity=0.9, matched_variant="CJ택배 앱을 설치하세요",
            categories=[CategoryCode.DELIVERY])]),
        signals=SignalAnalysis(status=AnalysisStatus.COMPLETED, signals=[signal("앱을 설치하세요")]))
    assert part.answer is _Answer.NO_RISK_FOUND
    assert part.brand is Brand.UNKNOWN
    assert part.category is Topic.UNKNOWN
    assert first_doubt(part) is None


def test_plain_login_form_does_not_automatically_mean_risk():
    part = environment_part('<form>로그인<input type="password"></form>')
    assert part.answer is _Answer.NO_RISK_FOUND
    assert first_doubt(part) is EnvDoubt.LOGIN_FORM
    assert part.details.reason.text == "전달된 HTML에서 로그인·인증 입력폼을 확인했습니다."


def test_environment_success_without_elements_differs_from_failure_unknown():
    good = environment_part("<p>환영합니다</p>")
    bad = environment_part("", failure=FailureCode.COLLECTION_FAILED)
    assert good.answer is _Answer.NO_RISK_FOUND
    assert first_doubt(good) is None
    assert good.details.reason.text == "제공된 HTML에서 분류 대상 요소를 확인하지 못했습니다."
    assert bad.answer in INCOMPLETE
    assert bad.brand is Brand.UNKNOWN
    assert bad.category is Topic.UNKNOWN
    assert first_doubt(bad) is None
    assert "페이지 접속·수집에 실패" in bad.details.reason.text


def test_environment_failure_preserves_multiple_elements_without_raw_html_or_visibility_claims():
    html = '<div hidden><a href="https://example.com/token-secret">앱 다운로드</a></div><form>로그인<input type="password" value="private-token"></form>'
    part = environment_part(html, analysis=PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT))
    assert part.answer in INCOMPLETE
    assert first_doubt(part) is EnvDoubt.APP_LINK
    for phrase in ("앱 다운로드 링크", "로그인·인증 입력폼", "분석 시간이 초과"):
        assert phrase in part.details.reason.text
    for phrase in ("<", "token", "https:", "화면", "노출", "제출했습니다"):
        assert phrase not in part.details.reason.text


@pytest.mark.parametrize("failure", list(FailureCode))
def test_all_explicit_failures_have_safe_deterministic_reasons(failure):
    message = message_part("", failure=failure)
    env = environment_part("<p>자료</p>", failure=failure)
    for part in (message, env):
        assert part.answer in INCOMPLETE
        assert first_doubt(part) is None
        assert part.details.reason.text
        assert "의심으로 처리했습니다" not in part.details.reason.text


def test_metadata_fallback_uses_exact_allowed_values_and_labels_source():
    failure = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.LLM_ERROR)
    exact = environment_part("<p>자료</p>", analysis=failure, brand="CJ택배", category="택배")
    assert exact.brand is Brand.CJ_PARCEL
    assert exact.category is Topic.PARCEL
    assert "격리 환경 전달 정보" in exact.details.reason.text
    assert "CJ택배" in exact.details.reason.text
    assert "HTML에서 CJ택배" not in exact.details.reason.text
    aliases = environment_part("<p>자료</p>", analysis=failure, brand="C.J택배", category="배송")
    assert aliases.brand is None
    assert aliases.category is None


def test_verified_page_values_take_precedence_even_on_failure():
    part = environment_part("<p>CJ택배 배송</p>", brand="DHL", category="쇼핑",
        analysis=PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.TIMEOUT,
            brand=Brand.CJ_PARCEL, category=Topic.PARCEL))
    assert part.brand is Brand.CJ_PARCEL
    assert part.category is Topic.PARCEL


def test_completed_unknown_page_values_survive_separate_collection_failure():
    part = environment_part(
        "<p>normal</p>",
        analysis=PageAnalysis(status=AnalysisStatus.COMPLETED),
        failure=FailureCode.PARTIAL_CONTENT,
    )

    assert part.answer in INCOMPLETE
    assert part.brand is Brand.UNKNOWN
    assert part.category is Topic.UNKNOWN


def test_explicit_unknown_page_metadata_survives_failed_analysis():
    part = environment_part(
        "<p>normal</p>",
        analysis=PageAnalysis(
            status=AnalysisStatus.FALLBACK,
            failure=FailureCode.LLM_ERROR,
        ),
    )

    assert part.answer in INCOMPLETE
    assert part.brand is Brand.UNKNOWN
    assert part.category is Topic.UNKNOWN
    assert "격리 환경 전달 정보" in part.details.reason.text


def test_page_signal_is_preserved_when_another_stage_failed():
    html = '<a href="/app">앱을 설치하세요</a>'
    inspection = inspect_html(html)
    candidate = signal(inspection.elements[0].element_id, source=EvidenceSource.OBSERVATION)
    part = build_environment_part(IsolatedPage(brand="unknown", category="unknown", info=html),
        inspection, PageAnalysis(status=AnalysisStatus.COMPLETED, signals=[candidate]),
        failure=FailureCode.PARTIAL_CONTENT)
    assert part.answer in INCOMPLETE
    assert first_doubt(part) is EnvDoubt.APP_LINK
    assert "앱 다운로드 링크" in part.details.reason.text
    assert "일부 자료만 확보" in part.details.reason.text


def test_missing_page_is_failure_even_with_completed_empty_analysis():
    part = build_environment_part(None, PageInspection("", (), None), PageAnalysis(status=AnalysisStatus.COMPLETED))
    assert part.answer in INCOMPLETE
    assert part.brand is None
    assert part.category is None
    assert first_doubt(part) is None


def test_failed_analysis_retains_grounded_request():
    part = message_part("앱을 설치하세요", failure=FailureCode.TIMEOUT)
    assert part.answer in INCOMPLETE
    assert first_doubt(part) is MessageDoubt.APP_INSTALL
    assert "앱을 설치" in part.details.reason.text
    assert "초과" in part.details.reason.text
    assert "의심으로 처리" not in part.details.reason.text


def test_failed_page_keeps_observed_form():
    part = environment_part('<form><input type="password"></form>',
        failure=FailureCode.PARTIAL_CONTENT)
    assert part.answer in INCOMPLETE
    assert first_doubt(part) is EnvDoubt.LOGIN_FORM
    assert part.details.reason.text


def test_failed_empty_page_does_not_invent_unknown_values():
    part = build_environment_part(None,
        PageInspection(text="", elements=(), failure=FailureCode.MISSING_RESULT),
        PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.MISSING_RESULT))
    assert part.answer in INCOMPLETE
    assert part.brand is None
    assert part.category is None
    assert first_doubt(part) is None
    assert part.details.reason.text
