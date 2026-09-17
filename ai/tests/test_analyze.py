from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.llm.analyze import analyze_message
from ai.types import (
    AnalysisStatus,
    CategoryCode,
    CategoryEvidence,
    EvidenceField,
    ExtractedMessage,
    PersuasionCode,
    PersuasionEvidence,
)


def fake_client(*, parsed=None, refusal=None, side_effect=None):
    parse = AsyncMock(side_effect=side_effect)
    if side_effect is None:
        parse.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(parsed=parsed, refusal=refusal)
                )
            ]
        )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
    )
    return client, parse


@pytest.mark.asyncio
async def test_analyze_message_returns_completed_structured_result():
    text = "Courier notice: package delivery is pending."
    parsed = ExtractedMessage(
        categories=[
            CategoryEvidence(code=CategoryCode.DELIVERY, evidence="package delivery")
        ],
        claimed_sender=EvidenceField(value="Courier", evidence="Courier"),
        claimed_purpose=EvidenceField(
            value="delivery notice", evidence="package delivery"
        ),
    )
    client, parse = fake_client(parsed=parsed)

    result = await analyze_message(text, client=client, model="test-model")

    assert result.analysis_status is AnalysisStatus.COMPLETED
    assert result.categories[0].code is CategoryCode.DELIVERY
    assert result.claimed_sender.value == "Courier"
    parse.assert_awaited_once()


@pytest.mark.asyncio
async def test_blank_message_skips_api_and_returns_fallback():
    client, parse = fake_client(parsed=ExtractedMessage())

    result = await analyze_message("   ", client=client, model="test-model")

    assert result.analysis_status is AnalysisStatus.FALLBACK
    assert result.categories == []
    assert result.claimed_sender == EvidenceField()
    parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_failure_returns_fixed_fallback():
    client, _ = fake_client(side_effect=RuntimeError("upstream failed"))

    result = await analyze_message("Confirm the notice.", client=client, model="test-model")

    assert result.analysis_status is AnalysisStatus.FALLBACK
    assert result.requested_actions == []
    assert result.persuasion_signals == []


@pytest.mark.asyncio
async def test_refusal_returns_fixed_fallback():
    client, _ = fake_client(parsed=None, refusal="cannot process")

    result = await analyze_message("Confirm the notice.", client=client, model="test-model")

    assert result.analysis_status is AnalysisStatus.FALLBACK
    assert result.claimed_purpose == EvidenceField()


@pytest.mark.asyncio
async def test_uncited_fields_are_removed_but_valid_fields_survive():
    text = "Traffic agency: an overdue penalty was issued. Confirm receipt now."
    parsed = ExtractedMessage(
        categories=[
            CategoryEvidence(code=CategoryCode.PENALTY, evidence="overdue penalty"),
            CategoryEvidence(
                code=CategoryCode.ACCOUNT_SECURITY, evidence="invented account"
            ),
        ],
        claimed_sender=EvidenceField(value="Traffic agency", evidence="Traffic agency"),
        claimed_purpose=EvidenceField(value="account check", evidence="invented purpose"),
        requested_actions=[
            EvidenceField(value="Confirm receipt", evidence="Confirm receipt"),
            EvidenceField(value="Install app", evidence="invented install"),
        ],
        persuasion_signals=[
            PersuasionEvidence(code=PersuasionCode.AUTHORITY, evidence="Traffic agency"),
            PersuasionEvidence(code=PersuasionCode.URGENCY, evidence="invented now"),
        ],
    )
    client, _ = fake_client(parsed=parsed)

    result = await analyze_message(text, client=client, model="test-model")

    assert result.analysis_status is AnalysisStatus.COMPLETED
    assert [item.code for item in result.categories] == [CategoryCode.PENALTY]
    assert result.claimed_sender.value == "Traffic agency"
    assert result.claimed_purpose == EvidenceField()
    assert [item.value for item in result.requested_actions] == ["Confirm receipt"]
    assert [item.code for item in result.persuasion_signals] == [
        PersuasionCode.AUTHORITY
    ]
