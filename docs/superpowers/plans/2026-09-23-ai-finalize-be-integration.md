# AI 최종 분석 통합과 BE 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BE가 최종 AI 함수 한 번으로 격리 페이지 분석까지 반영한 전체 결과를 받도록 코드와 연동 문서를 완성한다.

**Architecture:** 새 async `finalize_analysis()`가 기존 페이지 분석과 순수 조립 함수를 내부에서 연결한다. 기존 문자 결과를 재사용하고 `official=true`에서는 페이지 분석 없이 반환한다. BE는 URL 조사·페이지 수집·태스크 수명·응답 전송을 담당하며, 분석과 조립을 따로 호출할 필요가 없다.

**Tech Stack:** Python 3.11 이상, 기존 Pydantic 2·OpenAI 2, pytest·pytest-asyncio, Markdown. 의존성 추가 없음.

**Spec:** [승인된 설계](../specs/2026-09-23-be-ai-python-integration-design.md), 설계 커밋 `cc6ac55`.

## Global Constraints

- 작업 브랜치는 `feature/scenario-message-test-ai`, 커밋 메시지는 한글이다. 별도 worktree를 만들지 않는다.
- 코드·테스트는 `ai/` 안에서 수정하고 관련 Markdown 문서를 함께 수정한다.
- BE·FE·격리 서버 제품 코드는 각 담당자가 구현한다.
- 최종 `result`는 항상 `url.official`이다. 페이지 분석은 env의 분류·근거를 채우며 최종 boolean을 뒤집지 않는다.
- 기존 세 함수의 공개와 호환성을 유지한다. 새 함수의 반환은 기존 `AnalysisResponse`다.
- 기존 Enum, 입력 상한, 출처별 근거 검증, HTML 비실행, unknown/없음/null 구분을 유지한다.
- AI가 생성한 client는 AI가 닫고 주입 client는 호출자가 관리한다. `asyncio.CancelledError`는 그대로 전파한다.
- AI는 BE 모듈을 import하지 않는다. HTTP AI 서버·새 오류 envelope·원점수 임계값·재시도를 추가하지 않는다.
- 현재 `CALLBACK_DEADLINE_SECONDS=45.0`을 바꾸지 않으며 남은 시간에 최종 함수의 페이지 분석 시간도 포함해야 한다.
- 테스트는 외부 네트워크 없이 실행한다. 실제 LLM 정확도나 외부 연동 성공을 주장하지 않는다.

## Review Focus

1. official=true에 부분 HTML·실패 코드·이미 실패한 문자가 함께 도착해도 분석하지 않고 null 10개를 반환한다. Task 1 조기 반환 테스트.
2. 다른 요청의 조기 반환에서 얻은 전부-null MessagePart가 false 요청에 들어오면 안전으로 재사용하지 않고 결과 미제공으로 표현한다. Task 1 누락 테스트.
3. 부분 HTML과 TIMEOUT이 함께 있으면 관측 요소·메타데이터·시간 초과 사유를 모두 보존한다. Task 1 수집 실패 매개변수 테스트.
4. 페이지 분석 중 상위 요청이 취소되면 취소가 전달되고 AI 소유 client만 닫힌다. Task 1 실제 분석 경유 취소 테스트.
5. BE 예제에서 URL provider가 실패하거나 요청이 취소되면 남은 문자·수집 task를 정리한다. Task 2 예제 실행 테스트.

---

## 파일 구조와 실행 환경

| 파일 | 작업·책임 |
|---|---|
| `ai/src/ai/pipeline/finalize.py` | 새 최종 분석 함수 |
| `ai/src/ai/pipeline/__init__.py` | 공개 export 추가 |
| `ai/tests/test_pipeline_finalize.py` | 최종 함수의 동작·실패·수명 검증 |
| `ai/tests/test_revision_pipeline.py:351` | 기존 공개 함수 목록 검사 갱신 |
| `docs/ai-be-python-integration.md` | BE가 작성할 세 구간과 연결 예제 |
| `ai/tests/test_be_integration_examples.py` | 문서의 실제 연결 예제를 읽어 실행·검증 |
| `ai/src/ai/pipeline/README.md` | 새 권장 호출 경로와 기존 흐름 구분 |
| `docs/ai-be-final-result-schema.md` | 새 함수 안내, 기존 응답 계약 유지 |
| `docs/ai/Revisions-handoff.md` | AI 최종 분석과 BE 수집·오케스트레이션 책임 |

실행 전 `CLAUDE.md`, 승인된 설계, 이 계획을 읽는다. 기존 화이트리스트 정책은 legacy API에 남기고 사용자와 합의한 Revisions의 점수 기반 boolean 계약을 새 pipeline에 적용한다. 기존 계획 문서나 정책을 재작성하지 않는다.

모든 명령은 저장소 루트 PowerShell 기준이다. 현재 사용 가능한 전용 Python은 `ai/.venv/revisions/Scripts/python.exe`다. 환경이 없어졌으면 프로젝트의 기존 Python 환경을 확인한 뒤 `ai/` 안에 테스트 환경을 준비한다. 루트 가상환경을 교체하지 않는다.

```powershell
git status --short
git branch --show-current
$env:PYTHONPATH = (Resolve-Path ai/src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

사용자 변경이 있으면 보존한다. 계획 문서 자체는 코드 실행 전 검토용으로 커밋한다. 아래 체크박스는 실제 수행한 단계만 완료로 표시한다.

### Task 1: AI 최종 분석 함수와 회귀 검증

**Files:**
- Create: `ai/src/ai/pipeline/finalize.py`
- Modify: `ai/src/ai/pipeline/__init__.py`
- Create: `ai/tests/test_pipeline_finalize.py`
- Modify: `ai/tests/test_revision_pipeline.py:351`

**Interfaces:**
- Consumes: `async analyze_environment_part(page: IsolatedPage | None, *, failure: FailureCode | None = None, client: AsyncOpenAI | None = None, model: str | None = None) -> EnvironmentPart`.
- Consumes: `assemble_analysis(url: UrlAnalysis, message: MessagePart | None = None, env: EnvironmentPart | None = None) -> AnalysisResponse`.
- Produces: `async finalize_analysis(url: UrlAnalysis, message: MessagePart | None = None, page: IsolatedPage | None = None, *, failure: FailureCode | None = None, client: AsyncOpenAI | None = None, model: str | None = None) -> AnalysisResponse` from `ai.pipeline`.
- Test fixture: `ai/tests/conftest.py::make_parse_client`를 그대로 재사용한다.

- [x] **Step 1: 최종 호출 계약을 검증할 실패 테스트를 작성한다.**

`ai/tests/test_pipeline_finalize.py`를 다음 내용으로 작성한다. 함수 호출 검사에 더해 실제 페이지 분석·조립을 대체 SDK로 실행한다.

```python
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
```

- [x] **Step 2: 기존 공개 목록 검사를 확장하고 RED를 확인한다.**

`ai/tests/test_revision_pipeline.py`의 마지막 공개 API 테스트를 다음으로 교체한다. import와 기존 세 함수의 계약도 함께 확인한다.

```python
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
```

```powershell
& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -m pytest ai/tests/test_pipeline_finalize.py ai/tests/test_revision_pipeline.py -q
```

예상: 새 `ai.pipeline.finalize` 모듈 부재로 수집 실패. 모듈이 이미 있으면 실패 원인을 확인하고 기존 작업을 덮어쓰지 않는다.

- [x] **Step 3: 최소 구현과 export를 추가한다.**

`ai/src/ai/pipeline/finalize.py`:

```python
"""Finish AI analysis from a prior message result and collected page material."""

from openai import AsyncOpenAI

from ai.pipeline.analysis import analyze_environment_part
from ai.pipeline.results import assemble_analysis
from ai.types import AnalysisResponse, FailureCode, IsolatedPage, MessagePart, UrlAnalysis


async def finalize_analysis(
    url: UrlAnalysis,
    message: MessagePart | None = None,
    page: IsolatedPage | None = None,
    *,
    failure: FailureCode | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> AnalysisResponse:
    """Analyze supplied page material and return the complete response."""
    if url.official:
        return assemble_analysis(url)
    env = await analyze_environment_part(
        page, failure=failure, client=client, model=model,
    )
    return assemble_analysis(url, message, env)
```

`ai/src/ai/pipeline/__init__.py`:

```python
from ai.pipeline.analysis import analyze_environment_part, analyze_message_part
from ai.pipeline.finalize import finalize_analysis
from ai.pipeline.results import assemble_analysis

__all__ = [
    "analyze_environment_part", "analyze_message_part",
    "assemble_analysis", "finalize_analysis",
]
```

- [x] **Step 4: 새 테스트와 연관 회귀 테스트를 실행한다.**

```powershell
& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -m pytest ai/tests/test_pipeline_finalize.py ai/tests/test_revision_pipeline.py ai/tests/test_revision_results.py ai/tests/test_revision_integration.py -q
git diff --check
```

예상: 모두 통과. 기존 PageProposal·입력폼 검증을 바꾸지 않고 새 함수에 필요한 문제만 수정한다. TDD 중 실패·성공 결과를 기록한다.

- [x] **Step 5: 작업 단위 리뷰와 커밋을 완료한다.**

선택한 실행 방식의 명세·코드 품질 리뷰를 수행하고 발견된 문제를 수정·검증한다.

```powershell
git add -- ai/src/ai/pipeline/finalize.py ai/src/ai/pipeline/__init__.py ai/tests/test_pipeline_finalize.py ai/tests/test_revision_pipeline.py
git commit -m "feat(ai): 격리 분석을 포함한 최종 결과 호출 추가"
```

### Task 2: BE 사용 문서와 실행 가능한 연결 예제

**Files:**
- Create: `docs/ai-be-python-integration.md`
- Create: `ai/tests/test_be_integration_examples.py`
- Modify: `ai/src/ai/pipeline/README.md`
- Modify: `docs/ai-be-final-result-schema.md`
- Modify: `docs/ai/Revisions-handoff.md`

**Interfaces:**
- Consumes: Task 1의 `finalize_analysis()` 전체 시그니처와 기존 `async analyze_message_part(text: str, *, client=None, model=None) -> MessagePart`.
- Produces: 문서의 `async analyze_request(message_text: str, get_url_result: Callable[[], Awaitable[UrlAnalysis]], collect_page: Callable[[], Awaitable[tuple[IsolatedPage | None, FailureCode | None]]]) -> AnalysisResponse` 예제. BE 제품 함수가 아니라 참조 예제다.
- Produces: `response.model_dump(mode="json")`으로 전체 응답을 직렬화하는 사용법.

- [x] **Step 1: 문서 예제의 동작 검사부터 작성한다.**

`ai/tests/test_be_integration_examples.py`에 다음을 작성한다. 문서 문자열 모양을 고정하는 테스트가 아니라, BE가 복사할 비동기 예제의 결과·취소 정리를 검증한다. 메시지 분석만 대체하고 최종 함수와 페이지 실패 처리는 실제 구현을 실행한다.

```python
import asyncio
from pathlib import Path
import re
from unittest.mock import AsyncMock, Mock

import pytest

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
    monkeypatch.setattr(analysis, "create_client", Mock(
        side_effect=AssertionError("example tests must not contact an LLM")))
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
    assert response.env.answer is False
    assert "시간" in response.env.details.reason
    assert response.message.answer is False
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
            assert response.result is True
            for part in (response.message, response.env):
                assert part.brand is part.category is part.answer is None
                assert part.details.doubt is part.details.reason is None
        assert stopped[0].is_set() and stopped[1].is_set()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
```

- [x] **Step 2: 문서가 없어서 실패하는 것을 확인한다.**

```powershell
& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -m pytest ai/tests/test_be_integration_examples.py -q
```

예상: `docs/ai-be-python-integration.md` 부재로 실패. 파일이 이미 생겼다면 사용자가 작성했는지 확인하고 내용을 보존한다.

- [x] **Step 3: 세 구간과 하나의 연결 예제로 가이드를 작성한다.**

승인된 설계의 세 구간 코드를 가이드에 반영한다. 독립적으로 복사 가능한 import를 각 구간에 포함하고 입력 변수의 출처를 설명한다. 아래 연결 코드는 하나의 Python 코드 블록으로 넣는다.

```python
import asyncio
from collections.abc import Awaitable, Callable

from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import AnalysisResponse, FailureCode, IsolatedPage, UrlAnalysis


async def analyze_request(
    message_text: str,
    get_url_result: Callable[[], Awaitable[UrlAnalysis]],
    collect_page: Callable[
        [], Awaitable[tuple[IsolatedPage | None, FailureCode | None]]
    ],
) -> AnalysisResponse:
    message_task = asyncio.create_task(analyze_message_part(message_text))
    url_task = asyncio.create_task(get_url_result())
    page_task = asyncio.create_task(collect_page())
    tasks = (message_task, url_task, page_task)
    try:
        url = await url_task
        if url.official:
            return await finalize_analysis(url)
        message, collected = await asyncio.gather(message_task, page_task)
        page, failure = collected
        return await finalize_analysis(url, message, page, failure=failure)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

다음 내용을 가이드에 명시한다.

| 항목 | 작성할 구체적 내용 |
|---|---|
| 준비 | Python 3.11 이상, `python -m pip install ./ai`, 세 LLM 환경변수, startup의 `load_cases()`, client 소유권 |
| 1번 | URL 제거·마스킹 완료 본문을 `await analyze_message_part(message_text)`에 전달, 반환 MessagePart 보관 |
| 2번 | `UrlAnalysis.model_validate(raw_url_result)` 후 최종 함수에 전달. 실제 boolean, 비어 있지 않은 URL/domain, domain 표현 자동 변환 없음 |
| 점수 | score<=T true, 초과 false. 점수 경로·T·누락·오류 정책은 BE가 정하며 값이 없다고 false를 만들지 않음 |
| 3번 | `IsolatedPage.model_validate(raw_page)`와 FailureCode를 준비해 최종 함수 한 번 호출. full AnalysisResponse 수신 후 model_dump |
| 수집 상태 | 정상·수집 실패·시간 초과·부분 수집·시간 초과 전 일부 자료 확보의 다섯 조합을 설계 표대로 기록 |
| 결과 | result=official, true의 null 10개 유지, false의 완성된 두 part, 출처별 근거, false는 의심 |
| provider | get_url_result/collect_page는 BE가 작성할 async 함수 인자이며 저장소에 이미 구현된 함수가 아님 |
| 오류 | ValidationError와 URL provider 오류는 BE 오류 경로. 알려진 수집 실패는 failure로 전달. 요청 취소는 전파 |
| 병렬 흐름 | URL 조사·문자 분석·페이지 수집 병렬 시작. false 확정 후 최종 AI 호출에서 페이지 분석 수행 |
| 종료 | true는 다른 작업의 정상 완료 없이 최종 결과를 만들되 finally에서 취소 정리를 기다림 |
| 운영 경계 | 최종 분석 시간까지 포함한 deadline·저장·콜백·늦은 결과 처리·다중 URL 정책은 BE 책임. 현재 45.0초를 변경하지 않음 |
| 기존 코드 | split_message는 URL 분리만 확인됨. parse_urlscan_result의 url/title/brands만으로 AI 입력이 완성되지 않음 |
| 반환 활용 | dict 자체는 카카오 SkillResponse가 아니며 reason은 평문. null 삭제 옵션 금지 |

본문에 HTTP AI 수신 엔드포인트나 AI 내부 설정을 BE가 재구현하는 구문을 만들지 않는다. 수집 자료 JSON 예시의 HTML은 합성 자료로 명시한다. 판정·Enum 상세는 결과 계약 문서로 링크하고 복제하지 않는다.

- [x] **Step 4: 기존 문서의 진입점과 책임을 맞춘다.**

- `ai/src/ai/pipeline/README.md`: Revisions 기준을 우선 안내하고 문자 분석 → 최종 함수 호출 예제를 넣는다. 이전 decide/화이트리스트 기반 내용은 legacy 흐름으로 구분해 남기거나 기존 설계 링크로 대체한다. 두 정책을 혼합하지 않는다.
- `docs/ai-be-final-result-schema.md`: 기존 고정 커밋은 응답 계약의 기준으로 남기고 이번 함수는 현재 `../ai/src/ai/pipeline/finalize.py`로 연결한다. 공개 함수 3개라는 문장을 갱신해 4개를 표기한다. finalize를 권장, 나머지 페이지 분석·조립은 기존 호출 호환용으로 설명한다. true/false 연동 예제를 최종 함수 중심으로 바꾸고 temp2 현재 브랜치라는 오래된 표현을 제거한다. 응답 JSON과 Enum은 변경하지 않는다.
- `docs/ai/Revisions-handoff.md`: 새 함수 시그니처와 호출 가이드를 추가한다. BE는 완료한 문자 결과·수집 자료·실패 코드를 전달하고 AI가 페이지 분석 후 전체 결과를 만든다고 수정한다. true는 `await finalize_analysis(url)`. AI API가 전부 미구현 제안이라는 설명은 현재 상태에 맞추되 BE·FE 배포 완료를 주장하지 않는다. 기존 BE 체크박스를 구현 완료로 표시하지 않는다.

- [x] **Step 5: 정상 수집 연결 사례를 추가하고 문서 예제를 검증한다.**

Task 2 테스트 파일에 다음 테스트를 추가한다. 최종 함수의 API 성공만 확인하는 것과 별도로 문서의 provider 전달이 올바른지 실제 페이지 분석까지 검사한다.

```python
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
    monkeypatch.setattr(analysis, "create_client", lambda timeout: OwnedClient())
    monkeypatch.setenv("LLM_MODEL", "test")
    example["analyze_message_part"] = AsyncMock(return_value=source_message)
    response = await example["analyze_request"]("body", AsyncMock(return_value=url()),
        AsyncMock(return_value=(source_page, None)))
    assert response.message == source_message
    assert response.env.answer is True
    assert response.result is False
    parse.assert_awaited_once()
```

```powershell
& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -m pytest ai/tests/test_be_integration_examples.py ai/tests/test_pipeline_finalize.py -q
```

예상: 모든 예제 경로 통과, 실제 외부 요청 없음. 추가로 문서 내 짧은 Python 블록을 `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`로 compile하고 JSON 예시는 json.loads로 검증한다. 로컬 링크는 문서 디렉터리 기준 실재 여부를 검사한다. 아래 명령은 실행할 검증 코드다.

```powershell
@'
import ast
import json
from pathlib import Path
import re

paths = [Path(p) for p in (
    "docs/ai-be-python-integration.md",
    "docs/ai-be-final-result-schema.md",
    "docs/ai/Revisions-handoff.md",
    "ai/src/ai/pipeline/README.md",
)]
for path in paths:
    text = path.read_text(encoding="utf-8-sig")
    for code in re.findall(r"^```python\n(.*?)^```\s*$", text, re.S | re.M):
        compile(code, str(path), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    for data in re.findall(r"^```json\n(.*?)^```\s*$", text, re.S | re.M):
        json.loads(data)
    prose = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", text, flags=re.S | re.M)
    for target in re.findall(r"\]\(([^)]+)\)", prose):
        if "://" not in target and not target.startswith("#"):
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
print("Document syntax, JSON and local links passed")
'@ | & 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -
git diff --check
```

- [x] **Step 6: AI 전체 회귀·문서 리뷰를 통과하고 커밋한다.**

```powershell
& 'ai/.venv/revisions/Scripts/python.exe' -X utf8 -m pytest ai/tests -q
git diff --stat
git diff --name-only
```

예상: 기존 테스트와 신규 테스트 모두 통과. 과거 605개 통과 기록을 이번 실행 결과로 쓰지 않고 실제 개수와 시간을 기록한다. 문서의 서명·null 정책·책임 경계가 코드와 일치하는지 리뷰한다.

```powershell
git add -- docs/ai-be-python-integration.md ai/tests/test_be_integration_examples.py ai/src/ai/pipeline/README.md docs/ai-be-final-result-schema.md docs/ai/Revisions-handoff.md
git commit -m "docs(ai): BE 최종 분석 호출 예제와 인계 문서 정리"
```

## 최종 리뷰와 완료 보고

- [x] `superpowers:requesting-code-review`로 구현 시작 직전 SHA부터 최종 SHA까지 전체 변경을 리뷰한다. 승인된 설계·계획·테스트 결과·ai 코드 범위를 리뷰어에게 전달한다.
- [x] 리뷰에서 보완 사항이 확인되면 수정·검증·최종 리뷰를 반복한다. 이번 리뷰는 지적 없음으로 수정 단계가 필요하지 않았다.
- [x] 마지막 코드·실행 예제 변경 후 전체 AI 회귀를 통과했다. 이후에는 검증·완료 상태만 문서에 기록했으므로 같은 테스트를 반복하지 않는다.
- [x] `git diff --check`, `git status --short`, `git log -3 --oneline`으로 최종 상태·커밋을 확인한다. 사용자 변경은 별도로 남겨두고 이번 변경 파일만 커밋됐는지 확인한다.
- [x] 완료 보고에 단일 최종 호출, BE 가이드 경로, 실제 테스트·리뷰 결과, 한글 커밋을 포함한다. BE 제품 연동은 문서 인계 범위이며 새 PR·push·merge는 수행하지 않는다.

## 실행 인계

계획 검토 후 사용자가 BE 전달 문서 중심의 최소 보완으로 진행하도록 확인했다. `superpowers:executing-plans`로 직접 구현하고 작업별 별도 에이전트 리뷰 대신 `superpowers:requesting-code-review`의 최종 독립 리뷰를 수행한다. 코드·테스트 범위와 두 작업의 기능 요구사항은 유지한다. 현재 브랜치에 커밋을 남기며 새 PR·push·merge는 수행하지 않는다.

## 실행 기록

- Task 1: `4103761`, 새 함수 부재의 RED 확인 후 관련 테스트 265개 통과.
- Task 2: `f115229`, 가이드 부재의 RED 확인 후 신규 테스트 22개와 전체 AI 테스트 627개 통과(10.36초).
- 문서 Python 구문·JSON·로컬 링크 검증 통과. 세 짧은 호출 예제도 실제 실행해 부분 수집 실패와 true 조기 반환의 전체 응답을 검증했다.
- 테스트는 대체 SDK·provider와 합성 HTML로 수행했으며 실제 외부 연동 검증은 포함하지 않는다.
- 최종 독립 리뷰: `de55e79..f115229`, Critical·Important·Minor 모두 없음, 추가 수정 불필요. 실제 LLM 정확도·외부 연결·BE 점수 정책·BE 제품 연동은 별도 담당 범위로 유지한다. 기존 HTML 규칙 전체의 재평가는 하지 않았고 이번 연결에 필요한 실패·부분 자료·취소 경로를 대조했다.
