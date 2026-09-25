"""Independent message and collected-page analysis; scheduling belongs to BE."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from openai import APITimeoutError, AsyncOpenAI

from ai.kb.search import search_cases
from ai.llm._client import create_client
from ai.llm.analyze import analyze_message, fallback_analysis
from ai.llm.page import analyze_page
from ai.llm.signals import analyze_signals
from ai.page import PageInspection, inspect_html
from ai.pipeline.results import build_environment_part, build_message_part
from ai.types import (
    AnalysisStatus, CaseSearchResult, EnvironmentPart, FailureCode,
    IsolatedPage, MessagePart, PageAnalysis, SignalAnalysis,
)


SEARCH_TIMEOUT_SECONDS = 0.05
MAX_MESSAGE_CHARS = 8192


async def _search_with_budget(text: str) -> CaseSearchResult:
    # Cancellation stops the coroutine, not an already running worker thread.
    # search_cases only reads the bounded local KB; BE warms its cache at startup.
    return await asyncio.wait_for(
        asyncio.to_thread(search_cases, text), timeout=SEARCH_TIMEOUT_SECONDS
    )


def _failure_code(error: Exception) -> FailureCode:
    return (FailureCode.TIMEOUT if isinstance(error, (TimeoutError, APITimeoutError))
        else FailureCode.LLM_ERROR)


async def analyze_message_part(
    text: str, *, client: AsyncOpenAI | None = None, model: str | None = None,
) -> MessagePart:
    """Analyze a body whose URLs were already removed by the caller."""
    extracted = fallback_analysis()
    cases = CaseSearchResult(status=AnalysisStatus.FALLBACK)
    signals = SignalAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.INVALID_OUTPUT)
    failure = None
    if not text.strip():
        failure = FailureCode.EMPTY_INPUT
    elif len(text) > MAX_MESSAGE_CHARS:
        failure = FailureCode.INPUT_TOO_LARGE
    if failure is not None:
        return build_message_part(text, extracted, cases, signals, failure=failure)

    try:
        async with AsyncExitStack() as stack:
            llm = client
            if llm is None:
                llm = await stack.enter_async_context(create_client(30.0))
            tasks = (
                asyncio.create_task(analyze_message(text, client=llm, model=model)),
                asyncio.create_task(_search_with_budget(text)),
            )
            try:
                extraction_result, search_result = await asyncio.gather(
                    *tasks, return_exceptions=True
                )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            # A cancelled child must never be converted to a successful part or
            # permit a subsequent signal API call.
            for result in (extraction_result, search_result):
                if isinstance(result, asyncio.CancelledError):
                    raise result
            if isinstance(search_result, Exception):
                failure = _failure_code(search_result)
            else:
                cases = search_result
            if isinstance(extraction_result, Exception):
                signals = SignalAnalysis(status=AnalysisStatus.FALLBACK,
                    failure=_failure_code(extraction_result))
            else:
                extracted = extraction_result
            if extracted.analysis_status is AnalysisStatus.COMPLETED:
                signals = await analyze_signals(
                    text, extracted, None, case_search=cases, client=llm, model=model
                )
    except Exception as error:
        signals = SignalAnalysis(status=AnalysisStatus.FALLBACK, failure=_failure_code(error))
    return build_message_part(text, extracted, cases, signals, failure=failure)


async def analyze_environment_part(
    page: IsolatedPage | None, *, failure: FailureCode | None = None,
    client: AsyncOpenAI | None = None, model: str | None = None,
) -> EnvironmentPart:
    """Analyze only supplied page material or an explicit collection failure."""
    inspection = (inspect_html(page.info) if page is not None else
        PageInspection(text="", elements=(), failure=FailureCode.MISSING_RESULT))
    failure = failure or inspection.failure
    if failure is not None:
        analysis = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=failure)
        return build_environment_part(page, inspection, analysis, failure=failure)

    try:
        analysis = await analyze_page(inspection, client=client, model=model)
    except Exception as error:
        analysis = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=_failure_code(error))
    return build_environment_part(page, inspection, analysis)
