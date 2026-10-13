import asyncio
import json
import logging
from pathlib import Path

from openai import APIResponseValidationError, APITimeoutError, AsyncOpenAI, LengthFinishReasonError
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
    )


async def analyze_message(
    masked_text: str,
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> MessageAnalysis:
    if not masked_text.strip():
        return fallback_analysis(FailureCode.EMPTY_INPUT)

    try:
        llm = client or create_client(TIMEOUT_SECONDS)
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
            timeout=TIMEOUT_SECONDS,
        )
        message = response.choices[0].message
        if message.refusal:
            return fallback_analysis(FailureCode.REFUSED)
        if message.parsed is None:
            return fallback_analysis(FailureCode.INVALID_OUTPUT)

        extracted = _sanitize_extracted(
            masked_text, ExtractedMessage.model_validate(message.parsed))
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
    except Exception as exc:
        LOGGER.warning("message analysis failed: %s", type(exc).__name__)
        return fallback_analysis(FailureCode.LLM_ERROR)
