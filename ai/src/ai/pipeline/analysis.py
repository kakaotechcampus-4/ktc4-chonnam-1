"""Independent message and collected-page analysis; scheduling belongs to BE."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import logging
import time
from dataclasses import replace

from openai import APITimeoutError, AsyncOpenAI

from ai.kb.search import search_cases
from ai.links import has_link_mark, looks_like_link, message_links, remove_spans
from ai.llm._client import create_client
from ai.llm.analyze import TIMEOUT_SECONDS as EXTRACTION_TIMEOUT_SECONDS
from ai.llm.analyze import analyze_message, fallback_analysis, ground_analysis
from ai.llm.page import analyze_page
from ai.llm.signals import analyze_signals
from ai.page import PageInspection, inspect_html
from ai.pipeline.results import build_environment_part, build_message_part
from ai.types import (
    AnalysisStatus, CaseSearchResult, EnvironmentPart, FailureCode,
    IsolatedPage, MessageAnalysis, MessagePart, MessageReading, PageAnalysis,
    SignalAnalysis,
)

LOGGER = logging.getLogger(__name__)


# TF-IDF search is ~2ms warm, but the query embedding waits on the network for up
# to semantic.QUERY_TIMEOUT_S before falling back to TF-IDF. gather() waits for
# both, so this adds time only when extraction finishes before the search.
SEARCH_TIMEOUT_SECONDS = 2.5
MAX_MESSAGE_CHARS = 8192
# Any non-blank text: search_cases returns before touching the KB on blank input.
_WARM_UP_TEXT = "warm-up"


async def _search_with_budget(text: str) -> CaseSearchResult:
    # Cancellation stops the coroutine, not an already running worker thread.
    # search_cases reads the bounded local KB and one bounded embedding request.
    return await asyncio.wait_for(
        asyncio.to_thread(search_cases, text), timeout=SEARCH_TIMEOUT_SECONDS
    )


def warm_up_case_search() -> CaseSearchResult:
    """Fill the caches the case search uses, so no request pays the KB load.

    Runs search_cases itself rather than a loader, so whatever it caches (the
    cases and the TF-IDF index today) is filled by construction.
    """
    return search_cases(_WARM_UP_TEXT)


def _failure_code(error: Exception) -> FailureCode:
    return (FailureCode.TIMEOUT if isinstance(error, (TimeoutError, APITimeoutError))
        else FailureCode.LLM_ERROR)


async def read_message(
    text: str, *, client: AsyncOpenAI | None = None, model: str | None = None,
    timeout_s: float = EXTRACTION_TIMEOUT_SECONDS,
) -> MessageReading:
    """Read the original message once for its requests and links (ADR 0007).

    Replaces BE's `split_message`: the extraction model lists the links and
    code keeps only those written whole in `text`. There is no regex fallback,
    so a failed extraction returns no links with a fallback status.
    `unverified_links` tells BE that some link-like text was not accepted.
    """
    failure = (FailureCode.EMPTY_INPUT if not text.strip()
        else FailureCode.INPUT_TOO_LARGE if len(text) > MAX_MESSAGE_CHARS else None)
    started = client_ready = llm_done = time.perf_counter()
    if failure is not None:
        extracted = fallback_analysis(failure)
    else:
        try:
            async with AsyncExitStack() as stack:
                llm = client
                if llm is None:
                    llm = await stack.enter_async_context(
                        create_client(timeout_s))
                client_ready = time.perf_counter()
                extracted = await analyze_message(
                    text, client=llm, model=model, timeout_s=timeout_s, ground_urls=False)
                llm_done = time.perf_counter()
        except Exception as error:
            extracted = fallback_analysis(_failure_code(error))
    found = message_links(text, extracted.urls)
    if failure is None:
        # 상한은 LLM 호출에만 걸리므로 클라이언트 준비·정리 시간을 나눠 남긴다. 문자 내용은 남기지 않는다
        finished = time.perf_counter()
        LOGGER.warning(
            "message read elapsed=%.3fs client=%.3fs llm=%.3fs status=%s failure=%s links=%d",
            finished - started, client_ready - started, max(llm_done, client_ready) - client_ready,
            extracted.analysis_status.value, extracted.failure.value if extracted.failure else None, len(found),
        )
    message = remove_spans(text, [span for span, _ in found])
    # A span the model changed or cut short stays out of `links`, and a link it
    # skipped leaves its mark in the body. Neither may read as "no link".
    # A span with no address shape (a messenger id the model listed) is not a link.
    unverified = (
        any(looks_like_link(span) and (span not in text or span in message)
            for span in extracted.urls)
        or has_link_mark(message)
    )
    # Links now live in `links`; later steps read only the body without them.
    grounded = ground_analysis(message, extracted.model_copy(update={"urls": []}))
    return MessageReading(
        links=tuple({link.url: link for _, link in found}.values()),
        message=message,
        extracted=grounded,
        unverified_links=unverified,
    )


async def _extract(
    text: str, given: MessageAnalysis | None, client: AsyncOpenAI, model: str | None,
) -> MessageAnalysis:
    # A reading from read_message already ran the extraction model.
    if given is not None:
        return given
    return await analyze_message(text, client=client, model=model)


async def analyze_message_part(
    text: str, *, extracted: MessageAnalysis | None = None,
    client: AsyncOpenAI | None = None, model: str | None = None,
) -> MessagePart:
    """Analyze a body whose URLs were already removed by the caller.

    Pass `read_message`'s `message` as `text` and its `extracted` to reuse that
    reading instead of calling the extraction model again.
    """
    given, extracted = extracted, fallback_analysis()
    cases = CaseSearchResult(status=AnalysisStatus.FALLBACK)
    signals: SignalAnalysis | None = None
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
                asyncio.create_task(_extract(text, given, llm, model)),
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
                # 검색 예산 초과·실패를 사례 없음과 구분해 남긴다(#155)
                LOGGER.warning("message part case search failed: %s", type(search_result).__name__)
                failure = _failure_code(search_result)
            else:
                cases = search_result
            if isinstance(extraction_result, Exception):
                extracted = fallback_analysis(_failure_code(extraction_result))
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
    started = time.perf_counter()
    # 공격자가 만든 큰 HTML 검사가 다른 사용자의 요청을 막지 않도록 별도 스레드에서 돌린다
    inspection = (await asyncio.to_thread(inspect_html, page.info) if page is not None else
        PageInspection(text="", elements=(), failure=FailureCode.MISSING_RESULT))
    if page is not None:
        # 소요 시간과 개수만 남긴다. 주소와 본문은 남기지 않는다
        LOGGER.warning(
            "page inspection elapsed=%.3fs html_chars=%d text_chars=%d elements=%d failure=%s text_capped=%s",
            time.perf_counter() - started, len(page.info), len(inspection.text), len(inspection.elements),
            inspection.failure.value if inspection.failure else None, inspection.text_capped,
        )
    # Collector truncation leaves usable material; inspection limits still stop analysis.
    # 글자 상한만 넘긴 페이지는 요소를 끝까지 봤으므로 위험 근거를 찾는다. inspection.failure 가
    # 그대로 남아 결과는 partial 이고, 근거가 없어도 정상으로 확정되지 않는다.
    inspection_stop = None if inspection.text_capped else inspection.failure
    stop = inspection_stop or (failure if failure is not FailureCode.PARTIAL_CONTENT else None)
    if stop is not None:
        analysis = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=stop)
        return build_environment_part(page, inspection, analysis, failure=failure)

    started = time.perf_counter()
    try:
        analysis = await analyze_page(replace(inspection, failure=None), client=client, model=model)
    except Exception as error:
        analysis = PageAnalysis(status=AnalysisStatus.FALLBACK, failure=_failure_code(error))
    LOGGER.warning(
        "page llm elapsed=%.3fs status=%s failure=%s",
        time.perf_counter() - started, analysis.status.value,
        analysis.failure.value if analysis.failure else None,
    )
    return build_environment_part(page, inspection, analysis, failure=failure)
