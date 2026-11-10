"""에이전트 라우터 행동 평가.

ai/ 에서 실행한다. LLM_API_KEY·LLM_BASE_URL·LLM_MODEL 이 필요하다. 에이전트 모델은 AGENT_MODEL, 없으면 LLM_MODEL 이다.

    python eval/agent_router_eval.py                  # eval/reports/<날짜>-agent-router.md
    python eval/agent_router_eval.py --label <이름>   # eval/reports/<날짜>-agent-router-<이름>.md

문자마다 read_message 로 링크를 읽고, 같은 읽기로 조사를 두 번 돌린다(에이전트 끔, 켬).
BE 도구는 정해 둔 관측을 정해 둔 시간 뒤에 돌려주는 가짜이고, 카드 대신 카드가 읽는 근거만 꺼낸다
(링크마다 공식 대조, 링크가 없으면 주장 분석). 수집과 분석이 고정이라 달라지는 것은 에이전트의 선택뿐이다.

카드가 같은지는 여기서 재지 않는다. tests/test_agent_router.py 가 가짜 LLM 으로 확인한다.
가짜 도구의 이름·설명은 BE(main._analysis_tools)와 같아야 하고, 그 테스트 파일이 함께 확인한다.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ai.agent import Board, Tool, Trace, case_search_tool, investigation, official_number_tool
from ai.case.types import Conclusion
from ai.llm._client import required_env
from ai.pipeline import read_message
from ai.types import AnalysisStatus, AnswerState, DomainMatch, MessageReading

EVAL_DIR = Path(__file__).resolve().parent
DATASETS = ("smishing", "benign")
REPORTS = EVAL_DIR / "reports"
CONCURRENCY = 4
# 가짜 BE 도구 하나가 걸리는 시간. 실측이 아니라 정해 둔 값이다. 운영의 수집은 이보다 길다(상한 17초)
STUB_DELAY_S = 1.0
LIMIT_NOTE = (
    "BE 도구는 가짜다. 관측은 모든 문자에 같은 값이고 걸리는 시간도 정해 둔 값이다. "
    "에이전트가 본문을 보고 무엇을 먼저 고르는지와 붙는 지연만 잰다. 판정·카드 품질이 아니다."
)

# (이름, 설명, per_link, 시간이 걸리는지, 관측). 이름·설명·per_link 는 main._analysis_tools 와 같게 둔다
BE_TOOLS = (
    ("collect_link",
     "링크를 격리 환경에서 열어 이동 경로와 페이지를 수집한다. 링크가 있으면 링크마다 필요하다",
     True, True,
     {"reached": True, "redirects": 0, "has_page": True, "browser_used": False, "downloads": 0, "failed": False}),
    ("analyze_message_signals",
     "문자 본문의 위험 신호와 내세운 기관 이름을 읽는다. 링크가 있으면 필요하다",
     False, True,
     {"answer": AnswerState.NO_RISK_FOUND, "brand_named": False, "signals": 0}),
    ("check_official_domain",
     "수집한 링크 주소를 공식 도메인 목록과 대조한다. 링크가 있으면 링크마다 필요하다",
     True, False,
     {"official": DomainMatch.NOT_REGISTERED}),
    ("analyze_claims",
     "문자가 내세운 사칭 주장과 요구를 읽는다. 링크가 없으면 필요하다. "
     "링크가 있어도 기관·지인을 내세우거나 무언가를 요구하는 문자면 쓸모가 있다",
     False, True,
     {"conclusion": Conclusion.INCONCLUSIVE, "caution": False, "institution_claimed": False, "demands": 0}),
)
# 공식 대조는 BE 처럼 그 링크의 수집과 문자 분석을 함께 끌어온다
NEEDS = {"check_official_domain": ("collect_link", "analyze_message_signals")}


@dataclass(frozen=True)
class Run:
    evidence: list
    trace: Trace
    head_start_s: float  # 조사를 열고 board 를 받기까지. 에이전트의 첫 수를 기다린 시간이다
    exit_wait_s: float   # 근거를 다 꺼낸 뒤 조사가 닫히기까지. 에이전트를 기다려 준 시간이다
    total_s: float


def stub_tools(reading: MessageReading, utterance: str) -> tuple[Tool, ...]:
    """BE 가 넘기는 것과 같은 도구 목록. BE 도구의 실행만 가짜다."""
    tools: dict[str, Tool] = {}

    def canned(name: str, seen: dict, delay: float):
        async def run(board: Board, link: int | None) -> dict:
            await asyncio.gather(*(
                board.get(tools[need], link if tools[need].per_link else None)
                for need in NEEDS.get(name, ())
            ))
            await asyncio.sleep(delay)
            return seen

        return run

    for name, description, per_link, slow, seen in BE_TOOLS:
        tools[name] = Tool(name, description, per_link, canned(name, seen, STUB_DELAY_S if slow else 0.0), dict)
    return (*tools.values(), case_search_tool(reading.message), official_number_tool(utterance))


async def investigate(reading: MessageReading, utterance: str, model: str | None) -> Run:
    """조사를 한 번 돌린다. model 이 없으면 에이전트 없이 코드만 근거를 꺼낸다."""
    tools = {tool.name: tool for tool in stub_tools(reading, utterance)}
    started = time.perf_counter()
    async with investigation(reading, tuple(tools.values()), model=model) as board:
        entered = time.perf_counter()
        # 카드가 읽는 근거만 꺼낸다(BE 의 _link_cards·run_message_case 가 꺼내는 것)
        if reading.links:
            evidence = [await board.get(tools["check_official_domain"], n)
                        for n in range(1, len(reading.links) + 1)]
        else:
            evidence = [await board.get(tools["analyze_claims"])]
        pulled = time.perf_counter()
    closed = time.perf_counter()
    return Run(evidence, board.trace(), entered - started, closed - pulled, closed - started)


async def run_rows(rows: Sequence[tuple[str, str]], model: str) -> tuple[list[dict], list[str]]:
    """(결과, 건너뛴 행 id). BE 가 조사를 열지 않는 문자(읽기 실패·읽지 못한 링크만 있음)는 건너뛴다."""
    gate = asyncio.Semaphore(CONCURRENCY)
    results, skipped = [], []

    async def one(row_id: str, text: str) -> None:
        async with gate:
            reading = await read_message(text)
            if (reading.extracted.analysis_status is not AnalysisStatus.COMPLETED
                    or (not reading.links and reading.unverified_links)):
                skipped.append(row_id)
                return
            off = await investigate(reading, text, None)
            on = await investigate(reading, text, model)
            results.append({"id": row_id, "links": len(reading.links), "off": off, "on": on})

    await asyncio.gather(*(one(row_id, text) for row_id, text in rows))
    return sorted(results, key=lambda item: item["id"]), sorted(skipped)


def _spread(values: Sequence[float]) -> str:
    ordered = sorted(values)
    if not ordered:
        return "—"
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"{statistics.median(ordered):.2f} / {p95:.2f} / {ordered[-1]:.2f}"


def render_report(results: Sequence[dict], skipped: Sequence[str], meta: dict) -> str:
    """리포트에는 행 id 와 수치만 쓴다. 문자 내용은 쓰지 않는다."""
    on = [item["on"] for item in results]
    off = [item["off"] for item in results]
    lines = [
        "# 에이전트 라우터 행동 평가",
        "",
        f"실행일: {meta['date']} · 커밋: `{meta['commit']}` · 에이전트 모델: `{meta['model']}` · "
        f"행: {len(results)} (건너뜀 {len(skipped)}) · 가짜 도구 지연: {STUB_DELAY_S}초",
        "",
        f"> {LIMIT_NOTE}",
        "",
        "## ① 루프가 끝난 이유",
        "",
        "| stop | 행 |",
        "|---|---|",
    ]
    stops = Counter(run.trace.stop for run in on)
    lines += [f"| {stop} | {count} |" for stop, count in stops.most_common()]
    turns = Counter(run.trace.turns for run in on)
    lines += ["", "LLM 턴 수: " + ", ".join(f"{n}턴 {turns[n]}행" for n in sorted(turns)), ""]

    lines += [
        "## ② 누가 분석을 시작했나",
        "",
        "`agent+code` 는 에이전트가 먼저 부르고 코드가 이어 받은 분석, `code` 는 에이전트가 부르지 않아 "
        "코드가 돌린 분석, `agent` 는 에이전트만 부른 분석이다.",
        "",
        "| 도구 | agent+code | code | agent | code+agent |",
        "|---|---|---|---|---|",
    ]
    asked: dict[str, Counter] = defaultdict(Counter)
    for run in on:
        for step in run.trace.steps:
            asked[step.tool]["+".join(step.askers)] += 1
    lines += [
        f"| {name} | " + " | ".join(str(asked[name][kind]) for kind in ("agent+code", "code", "agent", "code+agent")) + " |"
        for name in sorted(asked)
    ]

    lines += [
        "",
        "## ③ 시간 (초, p50 / p95 / 최대)",
        "",
        "| 항목 | 에이전트 끔 | 에이전트 켬 |",
        "|---|---|---|",
        f"| 첫 수 기다림 | {_spread([run.head_start_s for run in off])} | {_spread([run.head_start_s for run in on])} |",
        f"| 끝난 뒤 기다림 | {_spread([run.exit_wait_s for run in off])} | {_spread([run.exit_wait_s for run in on])} |",
        f"| 전체 | {_spread([run.total_s for run in off])} | {_spread([run.total_s for run in on])} |",
        "",
        "## ④ 근거 일치",
        "",
    ]
    differing = [item["id"] for item in results if item["on"].evidence != item["off"].evidence]
    lines.append(f"카드가 읽는 근거가 같은 행: {len(results) - len(differing)}/{len(results)} (다르면 에이전트 버그다)")
    if differing:
        lines.append("다른 행: " + ", ".join(differing))
    failed = [item["id"] for item in results if item["on"].trace.stop == "llm_error"]
    if failed:
        lines += ["", "llm_error 행: " + ", ".join(failed)]
    if skipped:
        lines += ["", "건너뛴 행: " + ", ".join(skipped)]
    lines.append("")
    return "\n".join(lines)


def _commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=EVAL_DIR).stdout.strip()

    return git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain") else "")


def main(argv: Sequence[str] | None = None) -> int:
    import embed_cache  # noqa: F401  (평가 실행용 환경변수 적재)

    parser = argparse.ArgumentParser(description="에이전트 라우터 행동 평가")
    parser.add_argument("--label", help="같은 날 다시 잴 때 리포트 파일 이름에 붙일 이름")
    args = parser.parse_args(argv)
    # 끈 실행은 모델을 넘기지 않는다. 환경변수가 남아 있으면 켜지므로 여기서 꺼낸다
    model = os.environ.pop("AGENT_MODEL", "").strip() or required_env("LLM_MODEL")
    rows = [
        (f"{name}#{index}", json.loads(line)["text"])
        for name in DATASETS
        for index, line in enumerate(
            (EVAL_DIR / "datasets" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines(), 1)
        if line.strip()
    ]
    results, skipped = asyncio.run(run_rows(rows, model))
    meta = {"date": date.today().isoformat(), "commit": _commit(), "model": model}
    REPORTS.mkdir(exist_ok=True)
    suffix = f"-{args.label}" if args.label else ""
    path = REPORTS / f"{meta['date']}-agent-router{suffix}.md"
    path.write_text(render_report(results, skipped, meta), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
