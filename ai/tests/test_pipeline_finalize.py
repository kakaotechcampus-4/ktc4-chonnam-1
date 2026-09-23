import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import ai.pipeline as public
import ai.pipeline.analysis as analysis
import ai.pipeline.finalize as finalizer
from ai.types import (
    AnalysisResponse, Brand, EnvDoubt, EnvironmentDetails, EnvironmentPart,
    FailureCode, IsolatedPage, MessageDetails, MessageDoubt, MessagePart,
    PageProposal, Topic, UrlAnalysis,
)


def url(official=False):
    return UrlAnalysis(final_url="https://example.com/a",
                       domain="example.com", official=official)


def message(answer=True):
    return MessagePart(brand=Brand.CJ_LOGISTICS, category=Topic.PARCEL,
        answer=answer, details=MessageDetails(
            doubt=MessageDoubt.PARCEL_LOOKUP, reason="문자에서 배송 조회를 요청했습니다."))


def page():
    return IsolatedPage(brand="CJ대한통운", category="택배",
        info='<form><label>비밀번호<input type="password"></label></form>')


@pytest.fixture(autouse=True)
def no_new_client_or_message_analysis(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("unexpected client or message work"))
    monkeypatch.setattr(analysis, "create_client", forbidden)
    monkeypatch.setattr(analysis, "analyze_message_part", forbidden)
    monkeypatch.setattr(public, "analyze_message_part", forbidden)
    return forbidden


@pytest.mark.asyncio
async def test_true_skips_page_work_and_keeps_all_null_keys(monkeypatch):
    analyze = AsyncMock(side_effect=AssertionError("page analysis must be skipped"))
    monkeypatch.setattr(finalizer, "analyze_environment_part", analyze)
    partial_page = IsolatedPage(brand="unknown", category="unknown", info="<form>")
    response = await public.finalize_analysis(
        url(True), message(False), partial_page, failure=FailureCode.TIMEOUT)
    data = response.model_dump(mode="json")
    assert data["result"] is True
    assert set(data) == {"url", "message", "env", "result"}
    expected = {"brand": None, "category": None, "answer": None,
                "details": {"doubt": None, "reason": None}}
    assert data["message"] == data["env"] == expected
    assert AnalysisResponse.model_validate_json(response.model_dump_json()) == response
    analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_false_forwards_settings_once_and_preserves_parts(monkeypatch):
    source_page, source_message, client = page(), message(), object()
    env = EnvironmentPart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=True, details=EnvironmentDetails(doubt=EnvDoubt.LOGIN_FORM,
        reason="전달된 HTML에서 로그인 입력폼을 확인했습니다."))
    analyze = AsyncMock(return_value=env)
    monkeypatch.setattr(finalizer, "analyze_environment_part", analyze)
    response = await public.finalize_analysis(url(), source_message, source_page,
        failure=FailureCode.PARTIAL_CONTENT, client=client, model="test")
    analyze.assert_awaited_once_with(source_page, failure=FailureCode.PARTIAL_CONTENT,
                                    client=client, model="test")
    assert response.result is False
    assert response.message == source_message
    assert response.env == env


@pytest.mark.asyncio
async def test_real_page_analysis_produces_complete_response(make_parse_client):
    client, parse = make_parse_client(parsed=PageProposal())
    client.close = AsyncMock()
    source_message = message()
    response = await public.finalize_analysis(
        url(), source_message, page(), client=client, model="test")
    assert response.message == source_message
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert response.env.answer is True
    assert response.result is False
    assert AnalysisResponse.model_validate_json(response.model_dump_json()) == response
    parse.assert_awaited_once()
    assert parse.await_args.kwargs["response_format"] is PageProposal
    client.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_message", [None, MessagePart()])
async def test_missing_or_skipped_message_is_not_success(source_message):
    response = await public.finalize_analysis(url(), source_message)
    assert response.message.answer is False
    assert response.message.details.doubt is MessageDoubt.UNKNOWN
    assert "전달받지 못해" in response.message.details.reason
    assert response.env.answer is False
    assert response.env.details.doubt is EnvDoubt.UNKNOWN
    assert response.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,fragment", [
    (FailureCode.COLLECTION_FAILED, "수집"),
    (FailureCode.TIMEOUT, "시간"),
    (FailureCode.PARTIAL_CONTENT, "자료 전체"),
])
@pytest.mark.parametrize("has_page", [False, True])
async def test_collection_failure_keeps_message_and_partial_facts(failure, fragment, has_page):
    source_message = message()
    response = await public.finalize_analysis(
        url(), source_message, page() if has_page else None, failure=failure)
    assert response.message == source_message
    assert response.env.answer is False
    assert fragment in response.env.details.reason
    if has_page:
        assert response.env.brand is Brand.CJ_LOGISTICS
        assert response.env.category is Topic.PARCEL
        assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
        assert "격리 환경 전달 정보" in response.env.details.reason
        assert "HTML" in response.env.details.reason
    else:
        assert response.env.brand is Brand.UNKNOWN
        assert response.env.details.doubt is EnvDoubt.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [(TimeoutError(), "시간"), (RuntimeError(), "완료하지 못해")])
async def test_page_sdk_failure_keeps_completed_message(error, fragment, make_parse_client):
    client, _ = make_parse_client(side_effect=error)
    source_message = message()
    response = await public.finalize_analysis(
        url(), source_message, page(), client=client, model="test")
    assert response.message == source_message
    assert response.env.answer is False
    assert fragment in response.env.details.reason
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_client_ownership_and_cancellation(owned, cancel, monkeypatch, make_parse_client):
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def block(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    sdk, _ = make_parse_client(parsed=PageProposal(), side_effect=block if cancel else None)
    class Client:
        chat = sdk.chat
        closed = False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.closed = True
        async def close(self):
            self.closed = True
    client = Client()
    factory = Mock(return_value=client)
    monkeypatch.setattr(analysis, "create_client", factory)
    task = asyncio.create_task(public.finalize_analysis(url(), message(), page(),
        client=None if owned else client, model="test"))
    try:
        if cancel:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped.is_set()
        else:
            assert (await task).env.answer is True
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert client.closed is owned
    if owned:
        factory.assert_called_once_with(2.0)
    else:
        factory.assert_not_called()
