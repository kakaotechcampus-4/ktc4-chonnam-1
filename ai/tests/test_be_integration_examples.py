import asyncio
from pathlib import Path
import re
from unittest.mock import AsyncMock, Mock

import pytest

import ai.llm.page as page_analysis
import ai.pipeline.analysis as analysis
from ai.types import FailureCode, MessagePart, UrlAnalysis


@pytest.fixture
def example(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "docs/ai-be-python-integration.md"
    blocks = re.findall(r"^```python\n(.*?)^```\s*$",
                        path.read_text(encoding="utf-8-sig"), re.S | re.M)
    candidates = [code for code in blocks if "async def analyze_request(" in code]
    assert len(candidates) == 1
    namespace = {}
    exec(compile(candidates[0], str(path), "exec"), namespace)
    forbidden = Mock(side_effect=AssertionError("example tests must not contact an LLM"))
    monkeypatch.setattr(analysis, "create_client", forbidden)
    monkeypatch.setattr(page_analysis, "create_client", forbidden)
    return namespace


def url(official=False):
    return UrlAnalysis(final_url="https://example.com/a", domain="example.com", official=official)


@pytest.mark.asyncio
async def test_false_example_returns_complete_failure_result(example):
    example["analyze_message_part"] = AsyncMock(return_value=MessagePart())
    response = await example["analyze_request"]("body",
        AsyncMock(return_value=url()),
        AsyncMock(return_value=(None, FailureCode.TIMEOUT)))
    assert response.result is False
    assert response.env.answer is None
    assert "시간" in response.env.details.reason
    assert response.message.answer is None
    assert set(response.model_dump(mode="json")) == {"url", "message", "env", "result"}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["official", "url_error", "cancel"])
async def test_example_cleans_started_tasks_on_every_exit(example, outcome):
    started = [asyncio.Event() for _ in range(3)]
    stopped = [asyncio.Event() for _ in range(3)]
    async def block(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped[index].set()
    async def body(text):
        return await block(0)
    async def collect():
        return await block(1)
    async def investigate():
        await asyncio.gather(started[0].wait(), started[1].wait())
        if outcome == "url_error":
            raise ValueError("invalid URL result")
        if outcome == "cancel":
            return await block(2)
        return url(True)
    example["analyze_message_part"] = body
    task = asyncio.create_task(example["analyze_request"]("body", investigate, collect))
    try:
        if outcome == "cancel":
            await asyncio.wait_for(started[2].wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped[2].is_set()
        elif outcome == "url_error":
            with pytest.raises(ValueError, match="invalid URL result"):
                await asyncio.wait_for(task, 1)
        else:
            response = await asyncio.wait_for(task, 1)
            assert response.result is False
            for part in (response.message, response.env):
                assert part.brand is part.category is part.answer is None
                assert part.details.doubt is None
                assert part.details.reason
        assert stopped[0].is_set() and stopped[1].is_set()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_false_example_analyzes_collected_page(example, monkeypatch, make_parse_client):
    from ai.types import IsolatedPage, MessageDetails, MessageDoubt, PageProposal, Brand, Topic
    source_message = MessagePart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=True, details=MessageDetails(doubt=MessageDoubt.NONE, reason="명시적인 요구 없음"))
    source_page = IsolatedPage(brand="unknown", category="unknown", info="<p>안녕하세요</p>")
    client, parse = make_parse_client(parsed=PageProposal())
    class OwnedClient:
        async def __aenter__(self):
            return client
        async def __aexit__(self, *args):
            return None
    monkeypatch.setattr(page_analysis, "create_client", lambda timeout: OwnedClient())
    monkeypatch.setenv("LLM_MODEL", "test")
    example["analyze_message_part"] = AsyncMock(return_value=source_message)
    response = await example["analyze_request"]("body", AsyncMock(return_value=url()),
        AsyncMock(return_value=(source_page, None)))
    assert response.message == source_message
    assert response.env.answer is True
    assert response.result is False
    parse.assert_awaited_once()
