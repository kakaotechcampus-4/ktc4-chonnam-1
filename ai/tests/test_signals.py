import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError

from ai.llm.signals import analyze_signals, extract_signals
from ai.types import (
    AnalysisStatus,
    EvidenceField,
    EvidenceSource,
    FailureCode,
    MessageAnalysis,
    RiskSignal,
    RiskSignalCode,
    SignalProposal,
)

TEXT = "한진택배 확인부탁합니다"


def extracted() -> MessageAnalysis:
    return MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        categories=[],
        claimed_sender=EvidenceField(value="한진택배", evidence="한진택배"),
        claimed_purpose=EvidenceField(),
        requested_actions=[],
        persuasion_signals=[],
    )


def fake_client(*, parsed=None, refusal=None, side_effect=None):
    parse = AsyncMock(side_effect=side_effect)
    if side_effect is None:
        parse.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))
            ]
        )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
    )
    return client, parse


def run(coro):
    return asyncio.run(coro)


def test_returns_proposed_signals(load_observations):
    proposal = SignalProposal(
        signals=[
            RiskSignal(
                code=RiskSignalCode.CREDENTIAL_REQUEST,
                evidence_source=EvidenceSource.OBSERVATION,
                evidence_ref="form_inputs",
            )
        ]
    )
    client, _ = fake_client(parsed=proposal)

    result = run(
        extract_signals(
            TEXT, extracted(), load_observations("form"), client=client, model="m"
        )
    )

    assert [item.evidence_ref for item in result] == ["form_inputs"]


def test_refusal_returns_empty_list(load_observations):
    client, _ = fake_client(parsed=None, refusal="거부")

    result = run(
        extract_signals(
            TEXT, extracted(), load_observations("form"), client=client, model="m"
        )
    )

    assert result == []


def test_api_error_returns_empty_list(load_observations):
    client, _ = fake_client(side_effect=RuntimeError("boom"))

    result = run(
        extract_signals(
            TEXT, extracted(), load_observations("form"), client=client, model="m"
        )
    )

    assert result == []


def test_api_status_error_logs_status_code_and_keeps_existing_fallback(load_observations, caplog):
    """예외 클래스 이름·상태 코드만 로그에 남으면서도 기존 fallback(빈 목록,
    LLM_ERROR)은 그대로인지 확인한다. exc.code/exc.type/exc.request_id는
    민감정보 노출 가능성을 더 줄이기 위해 의도적으로 뺐다."""

    error = APIStatusError(
        "insufficient quota",
        response=httpx.Response(402, request=httpx.Request("POST", "https://example.invalid")),
        body={"code": "insufficient_quota", "type": "billing_error"},
    )
    client, _ = fake_client(side_effect=error)

    with caplog.at_level("WARNING", logger="ai.llm.signals"):
        analysis = run(
            analyze_signals(TEXT, extracted(), load_observations("form"), client=client, model="m")
        )

    assert analysis.status is AnalysisStatus.FALLBACK
    assert analysis.failure is FailureCode.LLM_ERROR
    assert analysis.signals == []

    [record] = caplog.records
    assert "APIStatusError" in record.message
    assert "status=402" in record.message
    assert TEXT not in record.message
    assert "insufficient quota" not in record.message
    assert "insufficient_quota" not in record.message
    assert "billing_error" not in record.message


def test_timeout_returns_empty_list(load_observations):
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(10)

    client, _ = fake_client(side_effect=never_returns)

    result = run(
        extract_signals(
            TEXT, extracted(), load_observations("form"), client=client, model="m"
        )
    )

    assert result == []


def test_blank_text_and_no_observations_skips_call():
    client, parse = fake_client(parsed=SignalProposal())

    result = run(extract_signals("   ", extracted(), None, client=client, model="m"))

    assert result == []
    parse.assert_not_awaited()


def test_observation_payload_excludes_screenshot(load_observations, monkeypatch):
    captured = {}

    async def capture(*args, **kwargs):
        captured["messages"] = kwargs["messages"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(parsed=SignalProposal(), refusal=None)
                )
            ]
        )

    client, _ = fake_client(side_effect=capture)

    run(
        extract_signals(
            TEXT, extracted(), load_observations("apk"), client=client, model="m"
        )
    )

    user_content = captured["messages"][1]["content"]
    assert "screenshot" not in user_content
    assert "permissions" in user_content
