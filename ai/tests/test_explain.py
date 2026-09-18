import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ai.llm.explain import explain, generate_explanation
from ai.types import (
    AnalysisStatus,
    CaseSearchResult,
    FinalState,
    ReasonCode,
    Verdict,
)


def fake_text_client(*, content=None, refusal=None, side_effect=None):
    create = AsyncMock(side_effect=side_effect)
    if side_effect is None:
        create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, refusal=refusal)
                )
            ]
        )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    ), create


def a_verdict(final_state=FinalState.SMISHING_SUSPECTED) -> Verdict:
    return Verdict(
        reason_code=ReasonCode.LOOKALIKE,
        carrier_name="한진택배",
        final_state=final_state,
    )


def test_all_reason_codes_have_template():
    for code in ReasonCode:
        text = generate_explanation(Verdict(reason_code=code))
        assert text and isinstance(text, str)


def test_official_but_risky_has_no_key_error():
    verdict = Verdict(
        reason_code=ReasonCode.OFFICIAL_BUT_RISKY,
        carrier_name="한진택배",
        final_state=FinalState.SMISHING_SUSPECTED,
    )

    text = generate_explanation(verdict)

    assert "한진택배" in text
    assert "위험 신호" in text


def test_template_covers_every_final_state():
    for state in FinalState:
        text = generate_explanation(Verdict(reason_code=ReasonCode.NO_URL, final_state=state))
        assert text.strip()


def test_llm_text_is_used_when_available(load_observations):
    client, _ = fake_text_client(content="한진택배 공식 주소와 다른 주소입니다.")

    text = asyncio.run(
        explain(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == "한진택배 공식 주소와 다른 주소입니다."


def test_llm_failure_falls_back_to_template(load_observations):
    client, _ = fake_text_client(side_effect=RuntimeError("boom"))

    text = asyncio.run(
        explain(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == generate_explanation(a_verdict())


def test_llm_refusal_falls_back_to_template(load_observations):
    client, _ = fake_text_client(content=None, refusal="거부")

    text = asyncio.run(
        explain(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == generate_explanation(a_verdict())


def test_blank_llm_output_falls_back_to_template(load_observations):
    client, _ = fake_text_client(content="   ")

    text = asyncio.run(
        explain(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == generate_explanation(a_verdict())
