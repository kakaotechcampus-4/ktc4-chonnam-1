import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import APIStatusError

from ai.llm.explain import explain_verdict, generate_explanation
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
        explain_verdict(
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
        explain_verdict(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == generate_explanation(a_verdict())


def test_api_status_error_logs_status_code_and_keeps_existing_fallback(load_observations, caplog):
    """예외 클래스 이름·상태 코드만 로그에 남으면서도 기존 fallback(템플릿
    문장)은 그대로인지 확인한다. exc.code/exc.type/exc.request_id는
    민감정보 노출 가능성을 더 줄이기 위해 의도적으로 뺐다."""

    error = APIStatusError(
        "insufficient quota",
        response=httpx.Response(402, request=httpx.Request("POST", "https://example.invalid")),
        body={"code": "insufficient_quota", "type": "billing_error"},
    )
    client, _ = fake_text_client(side_effect=error)

    with caplog.at_level("WARNING", logger="ai.llm.explain"):
        text = asyncio.run(
            explain_verdict(
                a_verdict(),
                load_observations("form"),
                CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
                client=client,
                model="m",
            )
        )

    assert text == generate_explanation(a_verdict())

    [record] = caplog.records
    assert "APIStatusError" in record.message
    assert "status=402" in record.message
    assert "insufficient quota" not in record.message
    assert "insufficient_quota" not in record.message
    assert "billing_error" not in record.message


def test_llm_refusal_falls_back_to_template(load_observations):
    client, _ = fake_text_client(content=None, refusal="거부")

    text = asyncio.run(
        explain_verdict(
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
        explain_verdict(
            a_verdict(),
            load_observations("form"),
            CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[]),
            client=client,
            model="m",
        )
    )

    assert text == generate_explanation(a_verdict())


def test_package_exports_do_not_shadow_submodules():
    # __init__.py 가 서브모듈과 같은 이름을 재export 하면 그 속성이 모듈에서
    # 함수로 덮인다. `import ai.llm.explain as m` 이 함수를 주게 되어
    # monkeypatch.setattr(module, ...) 패턴이 조용히 깨진다.
    import ai.llm

    for name in ("analyze", "explain", "signals"):
        assert isinstance(getattr(ai.llm, name), ModuleType), f"{name} 이 가려졌다"
