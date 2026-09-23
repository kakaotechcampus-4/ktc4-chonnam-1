"""Independent entrypoint failures, deadlines and resource ownership."""

import asyncio
import inspect
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import ai.pipeline as public
import ai.pipeline.analysis as module
from ai.types import (
    AnalysisStatus, Brand, CaseSearchResult, EnvDoubt, ExtractedMessage,
    FailureCode, IsolatedPage, MessageAnalysis, MessageDoubt, PageProposal,
    SignalAnalysis, SignalProposal, Topic,
)


TEXT = "CJ대한통운 배송 조회하세요"
HTML = '<form><input type="password"></form>'


class FakeClient:
    def __init__(self, parse=None):
        self.calls = []
        self.closed = False
        self.entered = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse or self.parse))

    async def parse(self, **kwargs):
        assert not self.closed
        self.calls.append(kwargs)
        parsed = kwargs["response_format"]()
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(refusal=None, parsed=parsed))])

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *args):
        self.closed = True


@pytest.fixture(autouse=True)
def local_search(monkeypatch):
    monkeypatch.setattr(module, "search_cases", lambda text: CaseSearchResult(
        status=AnalysisStatus.COMPLETED))


def page(info=HTML):
    return IsolatedPage(brand="CJ대한통운", category="택배", info=info)


@pytest.mark.asyncio
async def test_successful_no_match_is_not_retrieval_failure(monkeypatch):
    signals = AsyncMock(return_value=SignalAnalysis(status=AnalysisStatus.COMPLETED))
    monkeypatch.setattr(module, "analyze_signals", signals)
    part = await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    assert part.answer is True
    assert part.brand is Brand.CJ_LOGISTICS
    assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert signals.await_args.kwargs["case_search"].status is AnalysisStatus.COMPLETED
    assert signals.await_args.kwargs["case_search"].matches == []
    assert signals.await_args.args[2] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " \n", "가" * 8193], ids=["empty", "blank", "oversized"])
async def test_invalid_body_never_creates_client_or_searches(text, monkeypatch):
    factory, search = Mock(), Mock()
    monkeypatch.setattr(module, "create_client", factory)
    monkeypatch.setattr(module, "search_cases", search)
    part = await module.analyze_message_part(text)
    assert part.answer is False
    assert part.details.doubt is MessageDoubt.UNKNOWN
    factory.assert_not_called()
    search.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error, fragment", [(RuntimeError("disk"), "검색"), (TimeoutError(), "시간")])
async def test_search_failure_keeps_current_message_facts(error, fragment, monkeypatch):
    def failed_search(text):
        raise error
    monkeypatch.setattr(module, "search_cases", failed_search)
    signals = AsyncMock(return_value=SignalAnalysis(status=AnalysisStatus.COMPLETED))
    monkeypatch.setattr(module, "analyze_signals", signals)
    part = await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    assert part.answer is False
    assert part.brand is Brand.CJ_LOGISTICS
    assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert fragment in part.details.reason
    assert signals.await_args.kwargs["case_search"].status is AnalysisStatus.FALLBACK


@pytest.mark.asyncio
async def test_search_deadline_cancels_coroutine_but_keeps_extraction(monkeypatch):
    stopped = asyncio.Event()
    async def blocked_thread(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(module.asyncio, "to_thread", blocked_thread)
    monkeypatch.setattr(module, "SEARCH_TIMEOUT_SECONDS", 0.001)
    part = await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    assert stopped.is_set()
    assert part.answer is False
    assert part.brand is Brand.CJ_LOGISTICS
    assert "시간" in part.details.reason


@pytest.mark.asyncio
async def test_extraction_and_local_thread_search_overlap(monkeypatch):
    loop = asyncio.get_running_loop()
    extraction_started, search_started = asyncio.Event(), asyncio.Event()
    search_finished = threading.Event()
    loop_thread = threading.get_ident()
    async def extract(*args, **kwargs):
        extraction_started.set()
        await search_started.wait()
        return MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED)
    def search(text):
        assert extraction_started.is_set()
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(search_started.set)
        search_finished.set()
        return CaseSearchResult(status=AnalysisStatus.COMPLETED)
    monkeypatch.setattr(module, "analyze_message", extract)
    monkeypatch.setattr(module, "search_cases", search)
    part = await asyncio.wait_for(module.analyze_message_part(TEXT, client=FakeClient(), model="test"), 1)
    assert part.answer is True
    assert search_finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fallback", "error", "timeout"])
async def test_failed_extraction_skips_signal_api(failure, monkeypatch):
    extract = AsyncMock(return_value=MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK))
    if failure != "fallback":
        extract.side_effect = TimeoutError() if failure == "timeout" else RuntimeError()
    signals = AsyncMock()
    monkeypatch.setattr(module, "analyze_message", extract)
    monkeypatch.setattr(module, "analyze_signals", signals)
    part = await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    assert part.answer is False
    assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert "문자 분석" in part.details.reason
    if failure == "timeout":
        assert "시간" in part.details.reason
    signals.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [FailureCode.INVALID_OUTPUT, FailureCode.TIMEOUT, FailureCode.LLM_ERROR])
async def test_signal_failure_preserves_extracted_message(failure, monkeypatch):
    monkeypatch.setattr(module, "analyze_signals", AsyncMock(return_value=SignalAnalysis(
        status=AnalysisStatus.FALLBACK, failure=failure)))
    part = await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    assert part.answer is False
    assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert part.brand is Brand.CJ_LOGISTICS


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["message", "page"])
@pytest.mark.parametrize("owned", [False, True])
async def test_success_closes_only_owned_client(entrypoint, owned, monkeypatch):
    client = FakeClient()
    factory = Mock(return_value=client)
    monkeypatch.setattr(module, "create_client", factory)
    kwargs = {"model": "test", "client": None if owned else client}
    if entrypoint == "message":
        part = await module.analyze_message_part(TEXT, **kwargs)
        assert [call["response_format"] for call in client.calls] == [ExtractedMessage, SignalProposal]
    else:
        part = await module.analyze_environment_part(page(), **kwargs)
        assert [call["response_format"] for call in client.calls] == [PageProposal]
        assert part.details.doubt is EnvDoubt.LOGIN_FORM
    assert part.answer is True
    assert client.closed is owned
    assert client.entered == int(owned)
    if owned:
        factory.assert_called_once_with(2.0)
    else:
        factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["extraction", "signals", "page"])
@pytest.mark.parametrize("error", [RuntimeError("SDK failure"), TimeoutError()])
async def test_sdk_failure_closes_owned_client_and_preserves_facts(stage, error, monkeypatch):
    client = FakeClient()
    real_parse = client.parse
    async def parse(**kwargs):
        target = {"extraction": ExtractedMessage, "signals": SignalProposal, "page": PageProposal}[stage]
        if kwargs["response_format"] is target:
            client.calls.append(kwargs)
            raise error
        return await real_parse(**kwargs)
    client.chat.completions.parse = parse
    monkeypatch.setattr(module, "create_client", Mock(return_value=client))
    if stage == "page":
        part = await module.analyze_environment_part(page(), model="test")
        assert part.details.doubt is EnvDoubt.LOGIN_FORM
    else:
        part = await module.analyze_message_part(TEXT, model="test")
        assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    assert part.answer is False
    assert client.closed
    assert part.brand is Brand.CJ_LOGISTICS
    assert len(client.calls) == (2 if stage == "signals" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["message", "page"])
async def test_missing_model_closes_owned_client_without_sdk_calls(entrypoint, monkeypatch):
    client = FakeClient()
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(module, "create_client", Mock(return_value=client))
    part = (await module.analyze_message_part(TEXT) if entrypoint == "message"
        else await module.analyze_environment_part(page()))
    assert part.answer is False
    assert client.closed
    assert client.calls == []


@pytest.mark.asyncio
async def test_maximum_body_length_remains_analyzable():
    client = FakeClient()
    text = "a" * 8192
    part = await module.analyze_message_part(text, client=client, model="test")
    assert part.answer is True
    assert client.calls[0]["messages"][1]["content"] == text


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["message", "page"])
async def test_client_creation_failure_preserves_deterministic_facts(entrypoint, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    if entrypoint == "message":
        part = await module.analyze_message_part(TEXT)
        assert part.details.doubt is MessageDoubt.PARCEL_LOOKUP
    else:
        part = await module.analyze_environment_part(page())
        assert part.details.doubt is EnvDoubt.LOGIN_FORM
        assert "격리 환경 전달 정보" in part.details.reason
    assert part.answer is False
    assert part.brand is Brand.CJ_LOGISTICS
    assert part.category is Topic.PARCEL


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied_page", [None, page()])
async def test_collection_failure_skips_page_llm(supplied_page, monkeypatch):
    llm, factory = AsyncMock(), Mock()
    monkeypatch.setattr(module, "analyze_page", llm)
    monkeypatch.setattr(module, "create_client", factory)
    part = await module.analyze_environment_part(supplied_page, failure=FailureCode.COLLECTION_FAILED)
    assert part.answer is False
    assert "수집" in part.details.reason
    if supplied_page:
        assert part.brand is Brand.CJ_LOGISTICS
        assert part.details.doubt is EnvDoubt.LOGIN_FORM
    llm.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("info", ["", '<form><input type="password">'])
async def test_incomplete_html_skips_page_llm_and_preserves_metadata(info, monkeypatch):
    llm = AsyncMock()
    monkeypatch.setattr(module, "analyze_page", llm)
    part = await module.analyze_environment_part(page(info))
    assert part.answer is False
    assert part.brand is Brand.CJ_LOGISTICS
    if info:
        assert part.details.doubt is EnvDoubt.LOGIN_FORM
    llm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["extraction", "signals", "page"])
@pytest.mark.parametrize("owned", [False, True])
async def test_cancellation_propagates_and_closes_only_owned_client(stage, owned, monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    client = FakeClient()
    real_parse = client.parse
    async def parse(**kwargs):
        target = {"extraction": ExtractedMessage, "signals": SignalProposal, "page": PageProposal}[stage]
        if kwargs["response_format"] is not target:
            return await real_parse(**kwargs)
        client.calls.append(kwargs)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    client.chat.completions.parse = parse
    monkeypatch.setattr(module, "create_client", Mock(return_value=client))
    kwargs = {"client": None if owned else client, "model": "test"}
    call = module.analyze_environment_part(page(), **kwargs) if stage == "page" else module.analyze_message_part(TEXT, **kwargs)
    task = asyncio.create_task(call)
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert client.closed is owned
    assert len(client.calls) == (2 if stage == "signals" else 1)


@pytest.mark.asyncio
async def test_cancelled_message_joins_both_child_coroutines(monkeypatch):
    starts = [asyncio.Event(), asyncio.Event()]
    stops = [asyncio.Event(), asyncio.Event()]
    async def blocked(index, *args, **kwargs):
        starts[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            stops[index].set()
    monkeypatch.setattr(module, "analyze_message", lambda *a, **kw: blocked(0))
    monkeypatch.setattr(module, "_search_with_budget", lambda *a: blocked(1))
    signals = AsyncMock()
    monkeypatch.setattr(module, "analyze_signals", signals)
    task = asyncio.create_task(module.analyze_message_part(TEXT, client=FakeClient(), model="test"))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in starts)), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(event.is_set() for event in stops)
    signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_stage_waits_keep_declared_budgets(monkeypatch):
    budgets = []
    real_wait_for = asyncio.wait_for
    async def record_wait(awaitable, timeout):
        budgets.append(timeout)
        return await real_wait_for(awaitable, timeout)
    monkeypatch.setattr(asyncio, "wait_for", record_wait)
    await module.analyze_message_part(TEXT, client=FakeClient(), model="test")
    await module.analyze_environment_part(page(), client=FakeClient(), model="test")
    assert sorted(budgets) == [0.05, 1.5, 2.0, 2.0]


def test_public_surface_is_independent_and_assembly_is_pure():
    assert set(public.__all__) == {
        "analyze_message_part", "analyze_environment_part",
        "assemble_analysis", "finalize_analysis",
    }
    assert not inspect.iscoroutinefunction(public.assemble_analysis)
    assert inspect.iscoroutinefunction(public.finalize_analysis)
    assert set(inspect.signature(public.analyze_environment_part).parameters) == {
        "page", "failure", "client", "model",
    }
    assert set(inspect.signature(public.finalize_analysis).parameters) == {
        "url", "message", "page", "failure", "client", "model",
    }
