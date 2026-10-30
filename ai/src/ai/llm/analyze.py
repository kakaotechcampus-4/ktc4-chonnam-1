import asyncio
import json
import logging
from pathlib import Path

from openai import (
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    LengthFinishReasonError,
)
from pydantic import ValidationError

from ai.llm._client import create_client, required_env
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
        llm = client or create_client(timeout_s)
        model_name = model or required_env("LLM_MODEL")
        response = await asyncio.wait_for(
            llm.chat.completions.parse(
                model=model_name,
                messages=[
                    {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                    {"role": "user", "content": masked_text},
                ],
                response_format=ExtractedMessage,
                temperature=0,
            ),
            timeout=timeout_s,
        )
        message = response.choices[0].message
        if message.refusal:
            return fallback_analysis(FailureCode.REFUSED)
        if message.parsed is None:
            return fallback_analysis(FailureCode.INVALID_OUTPUT)

        extracted = _sanitize_extracted(
            masked_text, ExtractedMessage.model_validate(message.parsed),
            ground_urls=ground_urls)
        return MessageAnalysis(
            analysis_status=AnalysisStatus.COMPLETED,
            **extracted.model_dump(),
        )
    except asyncio.CancelledError:
        raise
    except (TimeoutError, APITimeoutError) as exc:
        LOGGER.warning("message analysis failed: %s", type(exc).__name__)
        return fallback_analysis(FailureCode.TIMEOUT)
    except (ValidationError, APIResponseValidationError,
            LengthFinishReasonError, json.JSONDecodeError) as exc:
        LOGGER.warning("message analysis failed: %s", type(exc).__name__)
        return fallback_analysis(FailureCode.INVALID_OUTPUT)
    except APIStatusError as exc:
        # 예외 클래스 이름과 상태 코드만 남긴다 — exc.code/exc.type/
        # exc.request_id/exc.message/exc.body는 제공자가 보낸 값이라 민감
        # 정보 노출 가능성을 더 줄이기 위해 남기지 않는다.
        LOGGER.warning(
            "message analysis failed: %s status=%s",
            type(exc).__name__, exc.status_code,
        )
        return fallback_analysis(FailureCode.LLM_ERROR)
    except Exception as exc:
        LOGGER.warning("message analysis failed: %s", type(exc).__name__)
        return fallback_analysis(FailureCode.LLM_ERROR)
