"""LLM 호출 예산 계약.

공유 팩토리에 타임아웃을 고정하면 모듈별 선언이 조용히 무시된다. 실제로
`_create_client()` 가 1.5초를 못 박고 있어 signals·explain 의 2.0초 예산이
작동하지 않았다. 그 회귀를 막는다.

근거: docs/latency-budget.md, docs/adr/0004-llm-call-budget-ownership.md
"""

import asyncio

import pytest

import ai.llm.analyze as analyze_module
import ai.llm.explain as explain_module
import ai.llm.signals as signals_module
from ai.llm import _client
from ai.types import AnalysisStatus, MessageAnalysis, ReasonCode, Verdict

TEXT = "한진택배 확인부탁합니다"


def _extracted() -> MessageAnalysis:
    return MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED)


def _invoke(module):
    if module is analyze_module:
        return module.analyze_message(TEXT, model="m")
    if module is signals_module:
        return module.extract_signals(TEXT, _extracted(), None, model="m")
    return module.explain_verdict(Verdict(reason_code=ReasonCode.NO_URL), model="m")


def test_budgets_match_the_latency_document():
    # docs/latency-budget.md: 메시지 시나리오 테스트는 단계별 30초.
    assert analyze_module.TIMEOUT_SECONDS == 30.0
    assert signals_module.TIMEOUT_SECONDS == 30.0
    assert explain_module.TIMEOUT_SECONDS == 2.0


def test_create_client_honours_the_requested_timeout(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")

    assert _client.create_client(2.0).timeout == 2.0
    assert _client.create_client(1.5).timeout == 1.5


@pytest.mark.parametrize(
    "module", [analyze_module, signals_module, explain_module]
)
def test_module_passes_its_own_budget_to_the_client(module, monkeypatch):
    # 선언된 예산이 실제로 클라이언트까지 도달하는지 본다. 팩토리가 값을
    # 고정하고 있으면 이 단언이 깨진다.
    seen = {}

    def fake_create(timeout):
        seen["timeout"] = timeout
        raise RuntimeError("생성 직후 멈춰도 각 모듈은 폴백으로 빠진다")

    monkeypatch.setattr(module, "create_client", fake_create)

    asyncio.run(_invoke(module))

    assert seen["timeout"] == module.TIMEOUT_SECONDS


def test_required_env_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        _client.required_env("LLM_MODEL")
