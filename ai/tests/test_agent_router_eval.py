"""에이전트 라우터 평가 도구가 가짜 LLM 으로 끝까지 도는지 본다. 실제 측정은 LLM 키가 있어야 한다."""

import json
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import agent_router_eval as router_eval  # noqa: E402
from ai.agent import investigation  # noqa: E402
from ai.types import (  # noqa: E402
    AnalysisStatus, FailureCode, LinkCandidate, LinkOrigin, MessageAnalysis, MessageReading,
)

BODY = "비밀번호를 입력하세요"
READINGS = {
    "링크 있는 문자": MessageReading(
        links=(LinkCandidate(url="https://evil.test/a", domain="evil.test", origin=LinkOrigin.MESSAGE),),
        message=BODY,
        extracted=MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED),
    ),
    "읽지 못한 문자": MessageReading(
        links=(), message="",
        extracted=MessageAnalysis(analysis_status=AnalysisStatus.FALLBACK, failure=FailureCode.LLM_ERROR),
    ),
}


def one_call_client(name, arguments):
    """첫 턴에 도구 하나를 부르고 다음 턴에 끝내는 가짜 LLM."""
    turns = [[SimpleNamespace(id="call-1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))]]

    async def create(**request):
        calls = turns.pop(0) if turns else None
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=calls))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def test_rows_run_with_the_agent_off_then_on_and_the_report_holds_no_message_text(monkeypatch):
    async def read(text):
        return READINGS[text]

    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.setattr(router_eval, "STUB_DELAY_S", 0.0)
    monkeypatch.setattr(router_eval, "read_message", read)
    monkeypatch.setattr(router_eval, "investigation", partial(
        investigation, client=one_call_client("collect_link", {"link": 1}),
    ))

    results, skipped = await router_eval.run_rows(
        [("smishing#1", "링크 있는 문자"), ("smishing#2", "읽지 못한 문자")], "agent-model",
    )
    report = router_eval.render_report(
        results, skipped, {"date": "2026-11-11", "commit": "abc1234", "model": "agent-model"},
    )

    [row] = results
    assert (row["off"].trace.stop, row["on"].trace.stop) == ("off", "done")
    assert "| done | 1 |" in report
    assert "| collect_link | 1 | 0 | 0 | 0 |" in report
    assert "| check_official_domain | 0 | 1 | 0 | 0 |" in report
    assert "카드가 읽는 근거가 같은 행: 1/1" in report
    assert "건너뛴 행: smishing#2" in report
    assert BODY not in report and "evil.test" not in report
