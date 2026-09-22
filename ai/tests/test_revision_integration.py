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
