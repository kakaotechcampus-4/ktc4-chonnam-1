"""Exercise the revision contract with real pipelines and synthetic SDK/HTML data."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import ai.page as page_module
import ai.pipeline.analysis as pipeline_module
import ai.pipeline.results as results_module
import ai.verdict as verdict_module
from ai.page import inspect_html
from ai.pipeline import analyze_environment_part, analyze_message_part, assemble_analysis
from ai.types import (
    AnalysisStatus, CaseMatch, CaseSearchResult, CategoryCode, CategoryEvidence,
    EnvDoubt, EvidenceField, EvidenceSource, ExtractedMessage, IsolatedPage,
    MessageDoubt, PageProposal, RiskSignal, RiskSignalCode, SignalProposal,
    UrlAnalysis,
)


TEXT = "배송 현황을 확인하세요"
LOGIN_HTML = (
    '<form>회원 로그인<label>계정<input name="account"></label>'
    '<label>비밀번호<input type="password"></label></form>'
)


def sdk_response(parsed):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(parsed=parsed, refusal=None))])


def url(official=False):
    return UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=official)


def page(info=LOGIN_HTML):
    return IsolatedPage(brand="unknown", category="택배", info=info)


@pytest.fixture(autouse=True)
def local_search(monkeypatch):
    monkeypatch.setattr(pipeline_module, "search_cases", lambda text: CaseSearchResult(
        status=AnalysisStatus.COMPLETED, matches=[]))
    monkeypatch.setattr(pipeline_module, "create_client", Mock(
        side_effect=AssertionError("integration tests must use fake SDK clients")))


def message_client(make_parse_client, proposal=None):
    return make_parse_client(side_effect=[sdk_response(ExtractedMessage()),
        sdk_response(proposal or SignalProposal())])[0]


def signal(code, source, evidence):
    return RiskSignal(code=code, evidence_source=source, evidence_ref=evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("text,quotes", [
    ("[Web발신] [CJ대한통운]배송불가&l;도로명불일치&g;앱 다운로드 주소지확인 부탁드립니다",
     ("앱 다운로드", "주소지확인")),
    ("앱 다운로드 후 주소 확인 부탁드립니다", ("앱 다운로드", "주소 확인")),
    ("&#xC571;·다 운.로-드 부탁드립니다", ("앱·다 운.로-드",)),
])
@pytest.mark.parametrize("propose", [False, True])
async def test_install_taxonomy_and_normalized_grounding_survive_pipeline(
    text, quotes, propose, make_parse_client,
):
    proposal = SignalProposal(signals=[signal(
        RiskSignalCode.INSTALL_PROMPT, EvidenceSource.MESSAGE, text)] if propose else [])
    result = await analyze_message_part(
        text, client=message_client(make_parse_client, proposal), model="test")
    assert result.details.doubt is MessageDoubt.APP_INSTALL
    assert result.answer is (not propose)
    assert all(quote in result.details.reason for quote in quotes)


@pytest.mark.asyncio
@pytest.mark.parametrize("html,doubt,answer", [
    ('<form><label>주소<input name="address"></label><label>계좌 비밀번호를 입력하세요'
     '<input type="password"></label></form>', EnvDoubt.ADDRESS_FORM, False),
    ('<form><input aria-label="아이디"><input type="password" '
     'aria-label="계좌 비밀번호를 입력하세요"></form>', EnvDoubt.LOGIN_FORM, False),
    ('<label for="pw">계좌 비밀번호를 입력하세요</label><form>'
     '<input id="pw" type="password"></form>', EnvDoubt.LOGIN_FORM, False),
    ('<form>계좌 비밀번호는 입력하지 마세요, 전화번호를 입력하세요'
     '<input type="password"><input name="phone"></form>', EnvDoubt.LOGIN_FORM, True),
    ('<form>계좌 비밀번호는 필요 없고 전화번호를 입력하세요'
     '<input type="password"><input name="phone"></form>', EnvDoubt.LOGIN_FORM, True),
    ('<form>전화번호는 입력하지 말고 계좌 비밀번호를 입력하세요'
     '<input type="password"><input name="phone"></form>', EnvDoubt.LOGIN_FORM, False),
    ('<label for="other">계좌 비밀번호를 입력하세요</label><form>'
     '<input id="pw" type="password"></form>', EnvDoubt.LOGIN_FORM, True),
])
async def test_field_grounding_and_subject_local_negation_survive_pipeline(
    html, doubt, answer, make_parse_client,
):
    inspected = inspect_html(html)
    proposal = PageProposal(signals=[signal(RiskSignalCode.CREDENTIAL_REQUEST,
        EvidenceSource.OBSERVATION, inspected.elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)
    result = await analyze_environment_part(page(html), client=client, model="test")
    assert result.details.doubt is doubt
    assert result.answer is answer
    if not answer:
        assert "금융 인증정보" in result.details.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("html,code,answer", [
    ('<a href="/manual.pdf">앱 설치하지 마세요, 자료를 다운로드하세요</a>',
     RiskSignalCode.INSTALL_PROMPT, True),
    ('<a href="/manual.pdf">앱 설치는 필요 없고 설명서를 다운로드하세요</a>',
     RiskSignalCode.INSTALL_PROMPT, True),
    ('<a href="/parcel">원격 지원 앱은 설치하지 마세요, 배송 앱을 설치하세요</a>',
     RiskSignalCode.REMOTE_CONTROL, True),
    ('<a href="/target">자료는 다운로드하지 말고 앱을 설치하세요</a>',
     RiskSignalCode.INSTALL_PROMPT, False),
    ('<a href="/target">앱을 설치하지 말고 원격 지원 앱에 연결하세요</a>',
     RiskSignalCode.REMOTE_CONTROL, False),
    ('<a href="/target">원격 지원 앱을 설치하고 연결하세요</a>',
     RiskSignalCode.REMOTE_CONTROL, False),
], ids=["denied-app-material", "unneeded-app-manual", "denied-remote-parcel",
        "later-app", "later-remote", "same-remote-coordination"])
async def test_page_app_subject_keeps_its_own_action(html, code, answer, make_parse_client):
    inspected = inspect_html(html)
    proposal = PageProposal(signals=[signal(
        code, EvidenceSource.OBSERVATION, inspected.elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)

    result = await analyze_environment_part(page(html), client=client, model="test")

    assert result.answer is answer


@pytest.mark.asyncio
@pytest.mark.parametrize("text,code,answer", [
    ("원격 지원 앱은 설치하지 마세요, 배송 앱을 설치하세요",
     RiskSignalCode.INSTALL_PROMPT, False),
    ("원격 지원 앱을 설치하지 말고 연결하세요", RiskSignalCode.REMOTE_CONTROL, False),
    ("앱을 설치하지 말고 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("앱 설치는 필요 없고 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("앱을 다운로드하고 설치해 주시기 바랍니다", RiskSignalCode.INSTALL_PROMPT, False),
    ("앱을 설치하고 다운로드하지 마세요", RiskSignalCode.INSTALL_PROMPT, True),
    ("앱을 설치하지 말고 자료를 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, True),
    ("원격 지원 앱은 필요 없고 배송 앱을 설치하세요", RiskSignalCode.REMOTE_CONTROL, True),
    ("앱은 필요 없고 다른 프로그램을 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, True),
    ("application 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("remote support application 설치하세요", RiskSignalCode.REMOTE_CONTROL, False),
])
async def test_page_app_action_chains_stop_at_independent_subject(
    text, code, answer, make_parse_client,
):
    html = f'<a href="/target">{text}</a>'
    inspected = inspect_html(html)
    proposal = PageProposal(signals=[signal(
        code, EvidenceSource.OBSERVATION, inspected.elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)

    result = await analyze_environment_part(page(html), client=client, model="test")

    assert result.answer is answer


@pytest.mark.asyncio
@pytest.mark.parametrize("text,code,answer", [
    ("앱을 지금 바로 설치해 주시기 바랍니다", RiskSignalCode.INSTALL_PROMPT, False),
    ("앱 설치는 필요 없고 원격 지원 앱에 바로 연결하세요", RiskSignalCode.REMOTE_CONTROL, False),
    ("앱을 설치하고 바로 연결하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("원격 지원 앱을 설치하지 말고 바로 연결하세요", RiskSignalCode.REMOTE_CONTROL, False),
    ("앱을 지금 설치하지 말고 자료를 바로 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, True),
])
async def test_page_app_local_adverbs_preserve_request_scope(text, code, answer, make_parse_client):
    html = f'<a href="/client.apk">{text}</a>'
    proposal = PageProposal(signals=[signal(
        code, EvidenceSource.OBSERVATION, inspect_html(html).elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)

    result = await analyze_environment_part(page(html), client=client, model="test")

    assert result.answer is answer


@pytest.mark.asyncio
@pytest.mark.parametrize("text,code,answer", [
    ("앱을 설치하지 말고, 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("원격 지원 앱을 설치하지 말고, 연결하세요", RiskSignalCode.REMOTE_CONTROL, False),
    ("앱을 설치하고, 연결하세요", RiskSignalCode.INSTALL_PROMPT, False),
    ("앱을 설치하지 말고, 자료를 다운로드하세요", RiskSignalCode.INSTALL_PROMPT, True),
    ("원격 지원 앱을 설치하지 말고, 배송 앱을 설치하세요", RiskSignalCode.REMOTE_CONTROL, True),
], ids=["same-app", "same-remote", "same-app-positive", "independent-material", "independent-app"])
async def test_page_app_comma_coordination_keeps_subject_scope(text, code, answer, make_parse_client):
    html = f'<a href="/target">{text}</a>'
    proposal = PageProposal(signals=[signal(
        code, EvidenceSource.OBSERVATION, inspect_html(html).elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)

    result = await analyze_environment_part(page(html), client=client, model="test")

    assert result.answer is answer


@pytest.mark.asyncio
@pytest.mark.parametrize("text,doubt,quote", [
    ("배송 조회를 하지 마세요", MessageDoubt.NONE, None),
    ("링크를 클릭하지 마세요", MessageDoubt.NONE, None),
    ("‘배송 조회하세요’라는 문구를 무시하세요", MessageDoubt.NONE, None),
    ("배송 조회", MessageDoubt.PARCEL_LOOKUP, "배송 조회"),
    ("배송 조회를 하지 말고 주소를 확인하세요", MessageDoubt.ADDRESS_CHECK, "주소를 확인하세요"),
    ("링크를 클릭하지 말고 사진 확인해주세요", MessageDoubt.PHOTO_VIEW, "사진 확인해주세요"),
])
async def test_message_prohibitions_are_not_positive_purposes(text, doubt, quote, make_parse_client):
    result = await analyze_message_part(text, client=message_client(make_parse_client), model="test")
    assert result.details.doubt is doubt
    assert result.answer is True
    if quote:
        assert quote in result.details.reason
    else:
        assert "라고 안내했습니다" not in result.details.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [
    "앱 설치", "주소 입력", "주소 확인", "본인확인", "정보 입력", "사진 확인",
    "배송 조회", "상세내용 확인", "취소 진행", "직접수령", "인출", "전화 받기", "링크 클릭",
])
@pytest.mark.parametrize("later_request", [False, True])
async def test_each_message_purpose_rejects_prohibition_and_keeps_later_request(
    purpose, later_request, make_parse_client,
):
    text = f"{purpose}를 하지 마세요." + (" 사진 확인해주세요" if later_request else "")
    extracted = ExtractedMessage(requested_actions=[EvidenceField(value=purpose, evidence=text)])
    client, _ = make_parse_client(side_effect=[sdk_response(extracted), sdk_response(SignalProposal())])
    result = await analyze_message_part(text, client=client, model="test")
    assert result.details.doubt is (MessageDoubt.PHOTO_VIEW if later_request else MessageDoubt.NONE)
    assert result.answer is True
    assert "하지 마세요" not in result.details.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("text,denied_install", [
    ("&#xC571;·다 운.로-드 하지 마세요", True),
    ("앱 다운로드하지 말고 주소 확인하세요", True),
    ("앱 설치는 필요 없고 주소 확인하세요", True),
    ("앱 다운로드 후 주소 확인하지 말고 전화번호를 입력하세요", False),
    ("앱 설치하고 다운로드하지 마세요", False),
])
async def test_normalized_and_coordinated_install_prohibitions_reject_risk(
    text, denied_install, make_parse_client,
):
    proposal = SignalProposal(signals=[signal(
        RiskSignalCode.INSTALL_PROMPT, EvidenceSource.MESSAGE, text)])
    result = await analyze_message_part(
        text, client=message_client(make_parse_client, proposal), model="test")
    assert result.answer is True
    if denied_install:
        assert result.details.doubt is not MessageDoubt.APP_INSTALL


@pytest.mark.asyncio
async def test_field_digest_preserves_associated_context_without_attribute_secrets(make_parse_client):
    html = (
        '<label for="pw">계좌 비밀번호를 입력하세요</label>'
        '<label for="other">UNRELATED_LABEL</label>'
        '<form action="https://example.com/ACTION_SECRET">'
        '<input aria-label="아이디" value="FIRST_SECRET">'
        '<input id="pw" type="password" aria-label="금융 인증" value="VALUE_SECRET" '
        'data-token="TOKEN_SECRET" formaction="https://example.com/FORM_SECRET">'
        '</form>'
    )
    captured = {}

    async def capture(**kwargs):
        captured.update(json.loads(kwargs["messages"][1]["content"]))
        return sdk_response(PageProposal())

    client, _ = make_parse_client(side_effect=capture)
    await analyze_environment_part(page(html), client=client, model="test")
    element_data = json.dumps(captured["elements"], ensure_ascii=False)
    assert "아이디" in element_data
    assert "금융 인증" in element_data
    assert "계좌 비밀번호를 입력하세요" in element_data
    assert "UNRELATED_LABEL" not in element_data
    for secret in ("ACTION_SECRET", "FIRST_SECRET", "VALUE_SECRET", "TOKEN_SECRET", "FORM_SECRET"):
        assert secret not in json.dumps(captured)


@pytest.mark.asyncio
@pytest.mark.parametrize("label,answer", [
    ("계좌 비밀번호 4자리를 입력하세요", False),
    ("계좌 비밀번호를 아래에 입력하세요", False),
    ("계좌 비밀번호 4자리를 아래에 입력하세요", False),
    ("계좌 비밀번호 4자리를 입력하지 마세요, 전화번호를 입력하세요", True),
    ("계좌 비밀번호는 필요 없고 전화번호를 입력하세요", True),
    ("계좌 비밀번호 4자리는 필요 없고 전화번호를 입력하세요", True),
    ("계좌 비밀번호는 입력하지 말고 카드 비밀번호 4자리를 입력하세요", False),
    ("계좌 비밀번호를 아래에 입력하지 말고 전화번호를 입력하세요", True),
])
async def test_qualified_financial_subject_keeps_its_own_action(label, answer, make_parse_client):
    html = f'<form><label>{label}<input type="password"></label></form>'
    proposal = PageProposal(signals=[signal(RiskSignalCode.CREDENTIAL_REQUEST,
        EvidenceSource.OBSERVATION, inspect_html(html).elements[0].element_id)])
    client, _ = make_parse_client(parsed=proposal)
    result = await analyze_environment_part(page(html), client=client, model="test")
    assert result.answer is answer


@pytest.mark.asyncio
@pytest.mark.parametrize("text,answer", [
    ("app install please now", False),
    ("app install now to continue", False),
    ("app install please", False),
    ("app install not required", True),
    ("app install nowadays", True),
])
async def test_english_imperatives_preserve_word_boundaries(text, answer, make_parse_client):
    proposal = SignalProposal(signals=[signal(
        RiskSignalCode.INSTALL_PROMPT, EvidenceSource.MESSAGE, text)])
    result = await analyze_message_part(
        text, client=message_client(make_parse_client, proposal), model="test")
    assert result.answer is answer
    assert result.details.doubt is (MessageDoubt.NONE if answer else MessageDoubt.APP_INSTALL)


def assert_null_parts(response):
    data = response.model_dump(mode="json")
    skipped = {"brand": None, "category": None, "answer": None,
        "details": {"doubt": None, "reason": None}}
    assert data["message"] == skipped
    assert data["env"] == skipped
    assert data["result"] is True
    assert data["url"]["official"] is True


@pytest.mark.asyncio
async def test_different_message_and_page_meanings_survive_assembly(make_parse_client):
    extracted = ExtractedMessage(
        categories=[CategoryEvidence(code=CategoryCode.DELIVERY, evidence="배송")],
        requested_actions=[EvidenceField(value="배송 조회", evidence=TEXT)],
    )
    message_client, _ = make_parse_client(side_effect=[
        sdk_response(extracted), sdk_response(SignalProposal())])
    page_client, _ = make_parse_client(parsed=PageProposal())
    message, env = await asyncio.gather(
        analyze_message_part(TEXT, client=message_client, model="test"),
        analyze_environment_part(page(), client=page_client, model="test"),
    )
    data = assemble_analysis(url(), message, env).model_dump(mode="json")
    assert data["message"]["details"]["doubt"] == "배송 조회"
    assert data["env"]["details"]["doubt"] == "로그인·인증 입력폼"
    assert data["result"] is False
    assert data["url"]["official"] is False
    assert data["message"]["answer"] is True
    assert data["env"]["answer"] is True
    assert TEXT in data["message"]["details"]["reason"]
    assert "HTML" in data["env"]["details"]["reason"]
    assert "악성 확정" not in data["env"]["details"]["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied_failures", [False, True])
async def test_official_assembly_skips_all_work_and_overrides_parts(supplied_failures, monkeypatch):
    parts = (await analyze_message_part(""), await analyze_environment_part(None)) if supplied_failures else ()
    if parts:
        assert all(part.answer is False for part in parts)
    forbidden = Mock(side_effect=AssertionError("official assembly must do no analysis"))
    for attribute in ("create_client", "search_cases", "inspect_html", "analyze_message", "analyze_page"):
        monkeypatch.setattr(pipeline_module, attribute, forbidden)
    assert_null_parts(assemble_analysis(url(True), *parts))
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message_risk,page_risk", [(False, False), (True, False), (False, True), (True, True)])
async def test_local_answers_cannot_change_false_official_or_copy_sources(
    message_risk, page_risk, make_parse_client,
):
    text = "앱을 설치하세요" if message_risk else "배송 완료 안내입니다"
    html = '<a href="/client.apk">앱을 설치하세요</a>' if page_risk else "<p>안녕하세요</p>"
    message_proposal = SignalProposal(signals=[signal(
        RiskSignalCode.INSTALL_PROMPT, EvidenceSource.MESSAGE, text)] if message_risk else [])
    inspected = inspect_html(html)
    page_proposal = PageProposal(signals=[signal(
        RiskSignalCode.INSTALL_PROMPT, EvidenceSource.OBSERVATION,
        inspected.elements[0].element_id)] if page_risk else [])
    env_client, _ = make_parse_client(parsed=page_proposal)
    message, env = await asyncio.gather(
        analyze_message_part(text, client=message_client(make_parse_client, message_proposal), model="test"),
        analyze_environment_part(page(html), client=env_client, model="test"),
    )
    response = assemble_analysis(url(), message, env)
    assert response.result is False
    assert response.message.answer is (not message_risk)
    assert response.env.answer is (not page_risk)
    assert response.message.details.doubt is (MessageDoubt.APP_INSTALL if message_risk else MessageDoubt.NONE)
    assert response.env.details.doubt is (EnvDoubt.APP_LINK if page_risk else EnvDoubt.NONE)
    if not message_risk:
        assert "앱" not in response.message.details.reason
    if not page_risk:
        assert "앱" not in response.env.details.reason


@pytest.mark.asyncio
async def test_reference_only_password_quote_is_rejected_after_real_signal_analysis(monkeypatch, make_parse_client):
    reference = "비밀번호를 입력하세요"
    monkeypatch.setattr(pipeline_module, "search_cases", lambda text: CaseSearchResult(
        status=AnalysisStatus.COMPLETED, matches=[CaseMatch(
            case_id="reference-only", similarity=0.99, matched_variant=reference)]))
    client, parse = make_parse_client(side_effect=[sdk_response(ExtractedMessage()),
        sdk_response(SignalProposal(signals=[signal(
            RiskSignalCode.CREDENTIAL_REQUEST, EvidenceSource.MESSAGE, reference)]))])
    message = await analyze_message_part("배송 완료 안내입니다", client=client, model="test")
    payload = json.loads(parse.await_args_list[1].kwargs["messages"][1]["content"])
    assert payload["reference_cases"][0]["matched_variant"] == reference
    assert reference not in payload["message"]
    response = assemble_analysis(url(), message)
    assert response.result is False
    assert response.message.answer is True
    assert response.message.details.doubt is MessageDoubt.NONE
    assert reference not in response.message.details.reason


@pytest.mark.asyncio
async def test_plain_login_rejects_sdk_risk_candidate(make_parse_client):
    inspected = inspect_html(LOGIN_HTML)
    client, _ = make_parse_client(parsed=PageProposal(signals=[signal(
        RiskSignalCode.CREDENTIAL_REQUEST, EvidenceSource.OBSERVATION,
        inspected.elements[0].element_id)]))
    env = await analyze_environment_part(page(), client=client, model="test")
    response = assemble_analysis(url(), env=env)
    assert response.result is False
    assert response.env.answer is True
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert "HTML" in response.env.details.reason
    assert "악성" not in response.env.details.reason
    assert "비밀번호" not in response.message.details.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("timed_out", ["message", "env"])
async def test_actual_timeout_preserves_other_completed_part(timed_out, make_parse_client):
    cancelled = asyncio.Event()

    async def parse(**kwargs):
        if kwargs["response_format"] is ExtractedMessage:
            return sdk_response(ExtractedMessage())
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    stalled, _ = make_parse_client(side_effect=parse)
    ready_page, _ = make_parse_client(parsed=PageProposal())
    message, env = await asyncio.wait_for(asyncio.gather(
        analyze_message_part(TEXT, client=stalled if timed_out == "message" else message_client(make_parse_client), model="test"),
        analyze_environment_part(page(), client=stalled if timed_out == "env" else ready_page, model="test"),
    ), timeout=4.0)
    response = assemble_analysis(url(), message, env)
    failed, completed = (response.message, response.env) if timed_out == "message" else (response.env, response.message)
    assert cancelled.is_set()
    assert failed.answer is False
    assert "시간이 초과" in failed.details.reason
    assert completed.answer is True
    assert "초과" not in completed.details.reason
    assert response.message.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert response.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "<span></span>" * (page_module.MAX_ELEMENTS + 1),
    "가" * (page_module.MAX_PAGE_TEXT_CHARS + 1),
], ids=["element-limit", "text-limit"])
async def test_partial_html_preserves_observed_form_without_claiming_completion(suffix, make_parse_client):
    client, parse = make_parse_client(parsed=PageProposal())
    message = await analyze_message_part(TEXT, client=message_client(make_parse_client), model="test")
    env = await analyze_environment_part(page(LOGIN_HTML + suffix), client=client, model="test")
    response = assemble_analysis(url(), message, env)
    assert response.message.answer is True
    assert response.env.answer is False
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert "HTML" in response.env.details.reason
    assert "전체를 확인하지 못해" in response.env.details.reason
    assert "<form>" not in response.env.details.reason
    assert response.result is False
    parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_html_byte_limit_rejects_before_observing_any_form(make_parse_client):
    client, parse = make_parse_client(parsed=PageProposal())
    env = await analyze_environment_part(
        page(LOGIN_HTML + "a" * page_module.MAX_HTML_BYTES), client=client, model="test")
    response = assemble_analysis(url(), env=env)
    assert response.env.answer is False
    assert response.env.details.doubt is EnvDoubt.UNKNOWN
    assert "전체를 확인하지 못해" in response.env.details.reason
    assert "HTML에서" not in response.env.details.reason
    assert response.result is False
    parse.assert_not_awaited()


class OwnedClient:
    def __init__(self, client):
        self.chat = client.chat
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["extraction", "signals", "page"])
async def test_official_assembly_does_not_wait_and_be_cancellation_closes_owned_client(
    stage, monkeypatch, make_parse_client,
):
    entered, stopped = asyncio.Event(), asyncio.Event()
    target = {"extraction": ExtractedMessage, "signals": SignalProposal, "page": PageProposal}[stage]

    async def parse(**kwargs):
        if kwargs["response_format"] is not target:
            return sdk_response(ExtractedMessage())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    injected, parse_mock = make_parse_client(side_effect=parse)
    owned = OwnedClient(injected)
    monkeypatch.setattr(pipeline_module, "create_client", lambda timeout: owned)
    call = (analyze_environment_part(page(), model="test") if stage == "page"
        else analyze_message_part(TEXT, model="test"))
    task = asyncio.create_task(call)
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert_null_parts(assemble_analysis(url(True)))
        assert not task.done()
        assert not owned.closed
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert owned.closed
    assert parse_mock.await_count == (2 if stage == "signals" else 1)
    assert task.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "none", "null"])
async def test_unknown_none_and_null_preserve_distinct_json_keys(state, make_parse_client):
    if state == "none":
        client, _ = make_parse_client(parsed=PageProposal())
        message, env = await asyncio.gather(
            analyze_message_part("안녕하세요", client=message_client(make_parse_client), model="test"),
            analyze_environment_part(page("<p>안녕하세요</p>"), client=client, model="test"),
        )
        response = assemble_analysis(url(), message, env)
    else:
        response = assemble_analysis(url(state == "null"))
    data = response.model_dump(mode="json")
    for key in ("message", "env"):
        assert set(data[key]) == {"brand", "category", "answer", "details"}
        assert set(data[key]["details"]) == {"doubt", "reason"}
        assert data[key]["brand"] == (None if state == "null" else "unknown")
        assert data[key]["category"] == (None if state == "null" else "unknown")
        assert data[key]["details"]["doubt"] == {"unknown": "unknown", "none": "없음", "null": None}[state]
        assert data[key]["answer"] is {"unknown": False, "none": True, "null": None}[state]
        if state == "unknown":
            assert "전달받지 못해" in data[key]["details"]["reason"]
            assert "초과" not in data[key]["details"]["reason"]


@pytest.mark.parametrize("official", [None, 0, 1, "true", "false", "", [], {}])
def test_invalid_official_never_produces_classification(official):
    with pytest.raises(ValidationError):
        assemble_analysis(url(official))


@pytest.mark.asyncio
@pytest.mark.parametrize("official", [False, True])
async def test_new_pipeline_never_calls_legacy_decide(official, monkeypatch, make_parse_client):
    forbidden = Mock(side_effect=AssertionError("legacy verdict must remain independent"))
    monkeypatch.setattr(verdict_module, "decide", forbidden)
    monkeypatch.setattr(pipeline_module, "decide", forbidden, raising=False)
    monkeypatch.setattr(results_module, "decide", forbidden, raising=False)
    client, _ = make_parse_client(parsed=PageProposal())
    message, env = await asyncio.gather(
        analyze_message_part(TEXT, client=message_client(make_parse_client), model="test"),
        analyze_environment_part(page(), client=client, model="test"),
    )
    response = assemble_analysis(url(official), message, env)
    assert response.result is official
    assert message.answer is True
    assert env.answer is True
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_real_sdk_stages_and_search_keep_existing_budgets(monkeypatch, make_parse_client):
    deadlines = []
    real_wait_for = asyncio.wait_for

    async def record_wait(awaitable, timeout):
        deadlines.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", record_wait)
    client, _ = make_parse_client(parsed=PageProposal())
    message = await analyze_message_part(TEXT, client=message_client(make_parse_client), model="test")
    env = await analyze_environment_part(page(), client=client, model="test")
    assert message.answer is True
    assert env.answer is True
    assert sorted(deadlines) == [0.05, 1.5, 2.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,doubt,category", [
    ("배송 현황을 확인하세요", MessageDoubt.PARCEL_LOOKUP, "택배"),
    ("배송 상태를 조회하세요", MessageDoubt.PARCEL_LOOKUP, "택배"),
    ("배송 현황 확인 완료입니다", MessageDoubt.NONE, "택배"),
    ("배송 현황 확인과 주문 취소를 해주세요", MessageDoubt.PARCEL_LOOKUP, "unknown"),
])
async def test_parcel_status_wording_keeps_completion_and_mixed_topic_boundaries(
    text, doubt, category, make_parse_client,
):
    result = await analyze_message_part(text, client=message_client(make_parse_client), model="test")
    assert result.answer is True
    assert result.details.doubt is doubt
    assert result.category.value == category
