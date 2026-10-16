"""Collector HTTP 응답부터 AI 환경 결과까지의 계약 (실제 서비스 연결 아님)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from ai.pipeline import finalize_analysis
from ai.types import (
    AnalysisResponse,
    AnswerState,
    DomainMatch,
    EnvDoubt,
    FailureCode,
    PageProposal,
    RiskSignalCode,
    UrlAnalysis,
)
from scanner.isolation_client import collect_url_isolated
from scanner.isolation_page import to_isolated_page


API_URL = "https://isolation.example.test"
URL = "https://example.test/track"
STAGE_TWO = "<html><body><p>stage two delivery status</p></body></html>"
BROWSER_HTML = "<html><body><p>browser rendered delivery status</p></body></html>"
FORM_HTML = (
    '<html><body><form><label for="pw">계좌 비밀번호</label>'
    '<input id="pw" type="password" name="pw"></form></body></html>'
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def isolation_env(monkeypatch):
    monkeypatch.setenv("ISOLATION_API_URL", API_URL)
    monkeypatch.setenv("ISOLATION_API_TOKEN", "test-secret")


@pytest.fixture
def payload():
    return {
        "input_url": URL,
        "final_url": URL,
        "redirect_chain": [],
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "html": STAGE_TWO,
        "title": "Delivery status",
        "elapsed_ms": 100,
        "failures": [],
        "browser": None,
    }


@pytest.fixture
def llm_client():
    # Mock only the SDK call: analyze_page's payload and validation remain real.
    parse = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=PageProposal()))
    ]))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))


def browser_payload(html):
    return {
        "trigger": "script_only_page",
        "final_url": URL,
        "navigation_chain": [],
        "html": html,
        "title": "Rendered status",
        "downloads": [],
        "blocked_requests": 0,
        "elapsed_ms": 200,
        "failures": [],
    }


async def finalize(page, failure, llm_client):
    return await finalize_analysis(
        url=UrlAnalysis(final_url=URL, domain="example.test", official=DomainMatch.UNRESOLVED),
        page=page,
        failure=failure,
        client=llm_client,
        model="contract-test-model",
    )


async def collect_and_finalize(payload, llm_client):
    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == API_URL + "/collect"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {"url": URL}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Leave parse_response at its real default implementation.
        collected = await collect_url_isolated(URL, client=client)
    page, failure = to_isolated_page(collected)
    response = await finalize(page, failure, llm_client)
    return collected, page, failure, response


def llm_input(llm_client):
    parse = llm_client.chat.completions.parse
    parse.assert_awaited_once()
    return json.loads(parse.await_args.kwargs["messages"][1]["content"])


async def test_clean_collect_response_reaches_page_analysis(payload, llm_client):
    _, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert page.info == STAGE_TWO
    assert page.brand is page.category is None
    assert failure is None
    assert llm_input(llm_client)["page_text"] == "stage two delivery status"
    assert isinstance(response, AnalysisResponse)
    assert response.env.answer is AnswerState.NO_RISK_FOUND
    assert response.env.details.reason.failures == []
    assert response.env.collected_at == page.collected_at
    assert response.result is False  # A clean page alone does not make the URL official.


async def test_browser_html_is_used_by_ai(payload, llm_client):
    payload["browser"] = browser_payload(BROWSER_HTML)
    _, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert page.info == BROWSER_HTML
    assert failure is None
    assert llm_input(llm_client)["page_text"] == "browser rendered delivery status"
    assert response.env.answer is AnswerState.NO_RISK_FOUND


async def test_empty_browser_html_falls_back_to_stage_two(payload, llm_client):
    payload["browser"] = browser_payload("")
    _, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert page.info == STAGE_TWO
    assert failure is None
    assert llm_input(llm_client)["page_text"] == "stage two delivery status"
    assert response.env.answer is AnswerState.NO_RISK_FOUND


async def test_partial_collection_preserves_grounded_risk_from_page_analysis(payload, llm_client):
    payload.update(html=FORM_HTML, failures=["html_truncated"])
    llm_client.chat.completions.parse.return_value.choices[0].message.parsed = PageProposal(
        signals=[{"code": "credential_request", "evidence_source": "observation", "evidence_ref": "element-0003"},
                 {"code": "install_prompt", "evidence_source": "observation", "evidence_ref": "element-0003"}])
    _, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert page.info == FORM_HTML
    assert failure is FailureCode.PARTIAL_CONTENT
    assert response.env.answer is AnswerState.PARTIAL
    assert response.env.details.reason.failures == [FailureCode.PARTIAL_CONTENT]
    assert any(item.value is EnvDoubt.LOGIN_FORM for item in response.env.details.doubts)
    assert [item.code for item in response.env.details.signals] == [RiskSignalCode.CREDENTIAL_REQUEST]
    assert response.env.details.signals[0].evidence == "계좌 비밀번호 입력 필드"
    assert response.env.collected_at == page.collected_at
    assert response.result is False
    assert llm_input(llm_client)["elements"][0]["element_id"] == "element-0003"


@pytest.mark.parametrize("failures", [["html_truncated"], []])
async def test_page_llm_error_preserves_collector_failure_and_facts(payload, llm_client, failures):
    payload.update(html=FORM_HTML, failures=failures)
    llm_client.chat.completions.parse.side_effect = RuntimeError("SDK unavailable")
    _, _, _, response = await collect_and_finalize(payload, llm_client)

    expected = ([FailureCode.PARTIAL_CONTENT] if failures else []) + [FailureCode.LLM_ERROR]
    assert response.env.answer is AnswerState.PARTIAL
    assert response.env.details.reason.failures == expected
    assert any(item.value is EnvDoubt.LOGIN_FORM for item in response.env.details.doubts)
    assert response.env.brand is response.env.category is None
    assert "unknown" not in response.env.details.reason.text
    assert response.env.details.signals == []
    assert response.result is False


async def test_missing_html_becomes_failed_environment(payload, llm_client):
    payload.update(html="", failures=["connection_failed"])
    _, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert page is None
    assert failure is FailureCode.COLLECTION_FAILED
    assert response.env.answer is AnswerState.FAILED
    assert response.env.details.reason.failures == [FailureCode.COLLECTION_FAILED]
    assert response.env.details.doubts == []
    llm_client.chat.completions.parse.assert_not_awaited()


async def test_uncollected_page_remains_not_run(llm_client):
    response = await finalize(None, None, llm_client)

    assert response.env.answer is AnswerState.NOT_RUN
    assert response.env.details.reason.failures == [FailureCode.MISSING_RESULT]
    assert response.env.details.doubts == []
    llm_client.chat.completions.parse.assert_not_awaited()


async def test_mismatched_response_url_is_not_analyzed(payload, llm_client):
    payload.update(input_url="https://other.example.test/", html=FORM_HTML)
    payload["browser"] = browser_payload(FORM_HTML)
    collected, page, failure, response = await collect_and_finalize(payload, llm_client)

    assert collected.input_url == URL
    assert collected.failures == ("url_mismatch",)
    assert collected.html == ""
    assert collected.browser is None
    assert page is None
    assert failure is FailureCode.COLLECTION_FAILED
    assert response.env.answer is AnswerState.FAILED
    assert response.env.details.reason.failures == [FailureCode.COLLECTION_FAILED]
    assert response.env.details.doubts == []
    assert response.env.details.signals == []
    llm_client.chat.completions.parse.assert_not_awaited()
