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
    env = await analyze_environment_part(
        page, failure=failure, client=client, model=model,
    )
    return assemble_analysis(url, message, env)
