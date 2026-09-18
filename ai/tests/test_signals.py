import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.llm.signals import extract_signals
from ai.types import (
    AnalysisStatus,
    EvidenceField,
    EvidenceSource,
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
