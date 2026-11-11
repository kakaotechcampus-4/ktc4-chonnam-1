import logging
from pathlib import Path

from openai import (
    AsyncOpenAI,
)

from ai.llm._client import create_client, failure_of, parse_answer, prompt_text, required_env
from ai.types import AnalysisStatus, EvidenceField, ExtractedMessage, FailureCode, MessageAnalysis


LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 30.0
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "parse_classify.md"


def fallback_analysis(failure: FailureCode | None = None) -> MessageAnalysis:
    return MessageAnalysis(
        analysis_status=AnalysisStatus.FALLBACK,
        failure=failure,
        categories=[],
        claimed_sender=EvidenceField(),
        claimed_purpose=EvidenceField(),
        requested_actions=[],
        persuasion_signals=[],
        urls=[],
    )


def _valid_field(masked_text: str, field: EvidenceField) -> EvidenceField:
    if not _has_evidence(masked_text, field.evidence):
        return EvidenceField()
    return field


def _has_evidence(masked_text: str, evidence: str | None) -> bool:
    return bool(evidence and evidence.strip() and evidence in masked_text)


def _sanitize_extracted(
    masked_text: str,
    extracted: ExtractedMessage,
    *,
    ground_urls: bool = True,
) -> ExtractedMessage:
    return ExtractedMessage(
        categories=[
            item
            for item in extracted.categories
            if _has_evidence(masked_text, item.evidence)
        ],
        claimed_sender=_valid_field(masked_text, extracted.claimed_sender),
        claimed_purpose=_valid_field(masked_text, extracted.claimed_purpose),
        requested_actions=[
            item
            for item in extracted.requested_actions
            if _has_evidence(masked_text, item.evidence)
        ],
        persuasion_signals=[
            item
            for item in extracted.persuasion_signals
            if _has_evidence(masked_text, item.evidence)
        ],
        urls=list(dict.fromkeys(
            url for url in extracted.urls
            if (_has_evidence(masked_text, url) if ground_urls else url.strip())
        )),
    )


def ground_analysis(text: str, analysis: MessageAnalysis) -> MessageAnalysis:
    """인용이 `text` 에 그대로 있는 칸만 남긴다. 링크를 뗀 본문에 다시 맞출 때 쓴다."""
    return MessageAnalysis(
        analysis_status=analysis.analysis_status,
        failure=analysis.failure,
        **_sanitize_extracted(text, analysis).model_dump(),
    )


async def analyze_message(
    masked_text: str,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
    ground_urls: bool = True,
) -> MessageAnalysis:
    """`timeout_s` 가 없으면 `TIMEOUT_SECONDS`. `ground_urls=False` 면 링크 구간을 원문
    대조 없이 넘긴다. 대조와 탈락 확인은 부른 쪽(`read_message`)이 한다 (ADR 0007)."""
    if not masked_text.strip():
        return fallback_analysis(FailureCode.EMPTY_INPUT)
    if timeout_s is None:
        timeout_s = TIMEOUT_SECONDS

    try:
        parsed = await parse_answer(
            ExtractedMessage,
            system=prompt_text(PROMPT_PATH),
            user=masked_text,
            client=client,
            make_client=create_client,
            model=model or required_env("LLM_MODEL"),
            timeout_s=timeout_s,
        )
        extracted = _sanitize_extracted(masked_text, parsed, ground_urls=ground_urls)
        return MessageAnalysis(
            analysis_status=AnalysisStatus.COMPLETED,
            **extracted.model_dump(),
        )
    except Exception as exc:  # noqa: BLE001 — LLM 이 실패해도 폴백 결과를 낸다 (원칙 3). 실패 코드는 failure_of 가 정한다
        return fallback_analysis(failure_of(exc, "message analysis", LOGGER))
