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
@pytest.mark.parametrize("official", [False, True])
async def test_finalize_preserves_supplied_parts_for_both_url_states(official, monkeypatch):
    source_message = message()
    env = EnvironmentPart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=True, details=EnvironmentDetails(doubt=EnvDoubt.NONE, reason="분석 완료"))
    analyze = AsyncMock(return_value=env)
    monkeypatch.setattr(finalizer, "analyze_environment_part", analyze)
    source_page = page()
    response = await public.finalize_analysis(url(official), source_message, source_page)
    assert response.result is official
    assert response.message == source_message
    assert response.env == env
    analyze.assert_awaited_once_with(source_page, failure=None, client=None, model=None)


@pytest.mark.asyncio
async def test_official_url_only_is_failure():
    response = await public.finalize_analysis(url(True))
    assert response.result is False
    assert response.message.answer is None
    assert response.env.answer is None
    assert response.message.details.reason
    assert response.env.details.reason


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
    assert response.message.answer is None
    assert response.message.details.doubt is None
    assert "전달받지 못" in response.message.details.reason
    assert response.env.answer is None
    assert response.env.details.doubt is None
    assert response.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,fragment", [
    (FailureCode.COLLECTION_FAILED, "수집"),
    (FailureCode.TIMEOUT, "시간"),
    (FailureCode.PARTIAL_CONTENT, "일부 자료"),
])
@pytest.mark.parametrize("has_page", [False, True])
@pytest.mark.parametrize("official", [False, True])
async def test_collection_failure_keeps_message_and_partial_facts(official, failure, fragment, has_page):
    source_message = message()
    response = await public.finalize_analysis(
        url(official), source_message, page() if has_page else None, failure=failure)
    assert response.message == source_message
    assert response.env.answer is None
    assert fragment in response.env.details.reason
    if has_page:
        assert response.env.brand is Brand.CJ_LOGISTICS
        assert response.env.category is Topic.PARCEL
        assert response.env.details.doubt is EnvDoubt.LOGIN_FORM
        assert "격리 환경 전달 정보" in response.env.details.reason
        assert "HTML" in response.env.details.reason
    else:
        assert response.env.brand is None
        assert response.env.details.doubt is None


@pytest.mark.asyncio
async def test_html_limit_preserves_message_and_sets_null(monkeypatch):
    import ai.page as page_module

    monkeypatch.setattr(page_module, "MAX_ELEMENTS", 2)
    source_message = message()
    source_page = IsolatedPage(
        brand="unknown",
        category="unknown",
        info='<form><input type="password"><div>미확인</div></form>',
    )

    result = await public.finalize_analysis(url(True), source_message, source_page)

    assert result.result is False
    assert result.message == source_message
    assert result.env.answer is None
    assert result.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert result.env.details.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [(TimeoutError(), "시간"), (RuntimeError(), "오류")])
async def test_page_sdk_failure_keeps_completed_message(error, fragment, make_parse_client):
    client, _ = make_parse_client(side_effect=error)
    source_message = message()
    response = await public.finalize_analysis(
        url(), source_message, page(), client=client, model="test")
    assert response.message == source_message
    assert response.env.answer is None
    assert fragment in response.env.details.reason
    assert response.env.details.doubt is EnvDoubt.LOGIN_FORM


@pytest.mark.asyncio
@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_client_ownership_and_cancellation(official, owned, cancel, monkeypatch, make_parse_client):
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
    task = asyncio.create_task(public.finalize_analysis(url(official), message(), page(),
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
