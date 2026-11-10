"""에이전트가 무엇을 고르든 카드와 저장 결과는 에이전트를 끈 실행과 같아야 한다 (ADR 0012).

실제 LLM 은 부르지 않는다. main.investigation 에 대본대로 답하는 가짜 클라이언트를 끼운다.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from ai.agent import investigation, loop, tools
from ai.case.types import ClaimView, Conclusion, MessageCaseResult, Scenario, StopAction
from ai.pipeline import assemble_analysis
from ai.types import (
    AnalysisStatus, AnswerState, Brand, CaseSearchResult, DomainMatch, EnvironmentDetails,
    EnvironmentPart, MessageDetails, MessagePart, Reason, UrlAnalysis,
)
from backend.src.server.investigation import see_claims, see_collection, see_official, see_signals
from scanner.isolation_page import to_isolated_page
from scanner.models import BrowserResult, CollectResult

FAMILY = "엄마 나야\n폰 고장나서 통화가 안돼\n급하게 결제할 게 있는데 50만원만 보내줘"
CJ = "https://www.cjlogistics.com/ko/tool/parcel/tracking"
EVIL = "https://evil.top/a"
EVIL2 = "https://evil2.top/b"
HTML = "<html><body>배송 조회</body></html>"
COLLECTED_AT = datetime(2026, 11, 11, tzinfo=timezone.utc)
CLEAN_ENV = EnvironmentPart(
    answer=AnswerState.NO_RISK_FOUND, details=EnvironmentDetails(reason=Reason(text="x")),
)
HANG = object()


def _page(link, final=None, chain=()):
    return CollectResult(link, final or link, chain, 200, "text/html", HTML, None, 1)


def _timeout(link):
    return CollectResult(link, None, (), None, None, "", None, 1, ("timeout",))


def _part(brand=None, risk=None):
    signals = [{"code": "credential_request", "evidence": risk}] if risk else []
    return MessagePart(
        brand=brand, answer=AnswerState.RISK_FOUND if risk else AnswerState.NO_RISK_FOUND,
        details=MessageDetails(signals=signals, reason=Reason(text="x")),
    )


def _caution():
    return MessageCaseResult(
        scenario=Scenario.LINK_SMISHING, conclusion=Conclusion.INCONCLUSIVE, caution=True,
        stop_actions=[StopAction.DONT_CLICK_LINK, StopAction.DONT_ENTER_INFO],
        claims=ClaimView(
            identity=[{"kind": "institution", "name": "청소행정과", "quote": "청소행정과입니다",
                       "listed_institution": False}],
            pretexts=["쓰레기 무단투기 신고가 접수되었습니다"],
            demands=[{"kind": "personal_info", "quote": "정보를 입력해 주세요"}],
        ),
        checked=["message_text"], unchecked=[{"item": "actual_sender", "reason": "cannot_verify"}],
        explanation="x",
    )


def _boom(*_):
    raise RuntimeError("synthetic")


@dataclass(frozen=True)
class Case:
    links: tuple[str, ...]
    message: str
    collect: Callable[[str], CollectResult]
    part: Callable[[], MessagePart]
    gate: Callable[[], MessageCaseResult] | None = None  # None 이면 conftest 의 어휘표 게이트
    unverified: bool = False
    clean_page: bool = False  # 페이지 LLM 없이도 공식 카드까지 가도록 페이지 결과를 정상으로 둔다


CASES = {
    "official_link": Case((CJ,), "[CJ대한통운] 배송 조회", _page, lambda: _part(Brand.CJ_LOGISTICS),
                          clean_page=True),
    "evil_link": Case((EVIL,), "비밀번호를 입력하세요", _page, lambda: _part(risk="비밀번호를 입력하세요")),
    "shortener": Case(
        ("https://bit.ly/test",), "[CJ대한통운] 배송 조회",
        lambda link: _page(link, "https://www.cjlogistics.com/", ("https://attacker.test/",)),
        lambda: _part(Brand.CJ_LOGISTICS),
    ),
    "held_with_caution": Case((EVIL,), "청소행정과입니다. 정보를 입력해 주세요", _timeout, _part, gate=_caution),
    "two_links": Case((EVIL, EVIL2), "정보를 입력해 주세요", _timeout, _part, gate=_caution, unverified=True),
    "no_link_family": Case((), FAMILY, _boom, _boom),
    "collection_failure": Case((EVIL,), "배송 조회", _boom, _part),
    "message_part_exception": Case((EVIL, EVIL2), "배송 조회", _page, _boom),
}

MANDATORY = [("collect_link", {"link": 1}), ("check_official_domain", {"link": 1})]
HOSTILE = {
    "calls_nothing": [[]],
    "calls_everything_twice": [
        [call for call in MANDATORY for _ in range(2)],
        [(name, {}) for name in ("analyze_claims", "analyze_message_signals", "search_similar_cases",
                                 "check_official_numbers") for _ in range(2)],
    ],
    "link_out_of_range": [[("collect_link", {"link": 9})]],
    "extra_url_argument": [[("collect_link", {"link": 1, "url": "https://attacker.test/"})]],
    "unknown_tool": [[("visit", {"url": "https://attacker.test/"})]],
    "mid_loop_exception": [[("analyze_claims", {})], RuntimeError("llm down")],
    "hangs_forever": [HANG],
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.setattr(
        tools, "search_cases", lambda text: CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[]),
    )


@pytest.fixture
def run_case(monkeypatch, stub_read_message):
    """정해 둔 문자를 가짜 수집·분석으로 돌린다. 응답, Job, 수집한 주소, 문자 게이트 호출 수를 돌려준다."""

    async def run(case: Case):
        collected, gate_calls = [], []

        async def collect(link):
            collected.append(link)
            return case.collect(link)

        async def part(text, **_):
            return case.part()

        async def gate(text, **kwargs):
            gate_calls.append(kwargs)
            return case.gate()

        async def clean_finalize(*, url, message, **_):
            return assemble_analysis(url, message, CLEAN_ENV)

        def pinned_page(result):
            page, failure = to_isolated_page(result)
            return page and page.model_copy(update={"collected_at": COLLECTED_AT}), failure

        stub_read_message(list(case.links), case.message, unverified=case.unverified)
        monkeypatch.setattr(main, "collect_url_isolated", collect)
        monkeypatch.setattr(main, "analyze_message_part", part)
        monkeypatch.setattr(main, "to_isolated_page", pinned_page)
        if case.gate:
            monkeypatch.setattr(main, "analyze_message_case", gate)
        if case.clean_page:
            monkeypatch.setattr(main, "finalize_analysis", clean_finalize)
        job_id = main.create_analysis_job("router-user")
        utterance = " ".join([case.message, *case.links])
        response = await main.run_analysis(utterance, job_id)
        return SimpleNamespace(
            response=json.loads(json.dumps(response).replace(job_id, "JOB")),
            job=main.ANALYSIS_JOBS[job_id], collected=collected, gate_calls=gate_calls,
        )

    return run


@pytest.fixture
def scripted_agent(monkeypatch):
    """에이전트를 켜고 LLM 을 대본대로 답하게 한다.

    턴은 [(도구 이름, 인자)] 목록, 올릴 예외, HANG(끝나지 않음) 중 하나다. 대본이 끝나면 호출 없이 답한다.
    """
    monkeypatch.setattr(loop, "TURN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(loop, "GRACE_S", 0.05)

    def install(*turns):
        client = SimpleNamespace(requests=[], hanging=asyncio.Event(), hang_reaped=False)

        async def create(**request):
            client.requests.append({**request, "messages": list(request["messages"])})
            turn = turns[len(client.requests) - 1] if len(client.requests) <= len(turns) else []
            if turn is HANG:
                client.hanging.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    client.hang_reaped = True
            if isinstance(turn, BaseException):
                raise turn
            calls = [
                SimpleNamespace(id=f"call-{len(client.requests)}-{index}", function=SimpleNamespace(
                    name=name, arguments=json.dumps(arguments),
                ))
                for index, (name, arguments) in enumerate(turn)
            ]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="안전한 문자입니다", tool_calls=calls or None),
            )])

        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        monkeypatch.setattr(main, "investigation", partial(investigation, client=client, model="scripted"))
        return client

    return install


def _askers(run):
    return {(step["tool"], step["link"]): step["askers"] for step in run.job["investigation"]["steps"]}


@pytest.mark.parametrize("script", HOSTILE.values(), ids=list(HOSTILE))
@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
async def test_hostile_agent_cannot_change_the_answer(run_case, scripted_agent, case, script):
    fixed = await run_case(case)

    scripted_agent(*script)
    routed = await run_case(case)

    assert routed.response == fixed.response
    assert routed.job["final_payload"] == fixed.job["final_payload"]
    assert routed.collected == fixed.collected == list(case.links)


async def test_each_link_is_judged_on_its_own_collection(run_case):
    case = Case((EVIL, EVIL2), "배송 조회", lambda link: _page(link) if link == EVIL else _timeout(link), _part)

    run = await run_case(case)

    last = run.job["final_payload"]
    assert last["url"]["domain"] == "evil2.top"
    assert last["env"]["details"]["reason"]["failures"] == ["collection_failed"]


async def test_agent_choices_show_up_as_agent_then_code(run_case, scripted_agent):
    scripted_agent(MANDATORY)

    run = await run_case(CASES["evil_link"])

    assert _askers(run) == {
        ("collect_link", 1): ("agent", "code"),
        ("check_official_domain", 1): ("agent", "code"),
        ("analyze_message_signals", None): ("code",),
    }
    assert (run.job["investigation"]["stop"], run.job["investigation"]["turns"]) == ("done", 2)


@pytest.mark.parametrize("agent_on", [False, True], ids=["agent_off", "asks_for_nothing"])
async def test_code_runs_every_mandatory_step_the_agent_left_out(run_case, scripted_agent, agent_on):
    if agent_on:
        scripted_agent([])

    run = await run_case(CASES["evil_link"])

    assert _askers(run) == {
        ("check_official_domain", 1): ("code",),
        ("collect_link", 1): ("code",),
        ("analyze_message_signals", None): ("code",),
    }
    assert run.job["investigation"]["stop"] == ("done" if agent_on else "off")


async def test_gate_the_agent_started_is_joined_not_rerun(run_case, scripted_agent):
    fixed = await run_case(CASES["held_with_caution"])
    scripted_agent([("analyze_claims", {})])

    routed = await run_case(CASES["held_with_caution"])

    assert routed.response == fixed.response
    assert len(routed.gate_calls) == len(fixed.gate_calls) == 1
    assert _askers(routed)[("analyze_claims", None)] == ("agent", "code")


async def test_llm_receives_observations_but_no_address_or_page(run_case, scripted_agent):
    client = scripted_agent(
        [*MANDATORY, ("analyze_message_signals", {}), ("analyze_claims", {})],
        [("check_official_numbers", {})],
    )

    await run_case(CASES["evil_link"])

    answers = [json.loads(message["content"]) for message in client.requests[1]["messages"]
               if message["role"] == "tool"]
    assert answers[:3] == [
        {"reached": True, "redirects": 0, "has_page": True, "browser_used": False, "downloads": 0,
         "failed": False},
        {"official": "not_registered"},
        {"answer": "risk_found", "brand_named": False, "signals": 1},
    ]
    sent = json.dumps(client.requests, ensure_ascii=False)
    assert "evil.top" not in sent
    assert "배송 조회" not in sent


async def test_trace_and_log_name_tools_but_no_text_or_address(run_case, scripted_agent, capsys):
    scripted_agent(MANDATORY)

    run = await run_case(CASES["evil_link"])

    [line] = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[AGENT]")]
    assert line.startswith("[AGENT] stop=done turns=2 steps=collect_link#1/agent+code/done/")
    recorded = line + json.dumps(run.job["investigation"], ensure_ascii=False)
    assert "evil" not in recorded
    assert "비밀번호" not in recorded


async def test_cancelled_request_reaps_the_agent_and_every_analysis(
    monkeypatch, stub_read_message, scripted_agent,
):
    started = {name: asyncio.Event() for name in ("collector", "message")}
    stopped = set()

    async def hold(name):
        started[name].set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(name)

    stub_read_message([EVIL], "본문")
    monkeypatch.setattr(main, "collect_url_isolated", lambda link: hold("collector"))
    monkeypatch.setattr(main, "analyze_message_part", lambda text, **_: hold("message"))
    client = scripted_agent([("check_official_numbers", {})], HANG)
    monkeypatch.setattr(loop, "TURN_TIMEOUT_S", 30)
    request = asyncio.create_task(main.run_analysis("본문 " + EVIL))
    await asyncio.wait_for(
        asyncio.gather(client.hanging.wait(), *(event.wait() for event in started.values())), 1,
    )

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert client.hang_reaped
    assert stopped == {"collector", "message"}


async def test_eval_offers_the_agent_the_same_tool_menu_as_the_backend(monkeypatch, stub_read_message):
    monkeypatch.syspath_prepend(str(Path(main.__file__).resolve().parent / "ai" / "eval"))
    import agent_router_eval

    stub_read_message([EVIL], "본문")
    reading = await main.read_message("본문 " + EVIL)
    _, real = main._analysis_tools("본문 " + EVIL, reading, 0.0)

    def menu(offered):
        return [(tool.name, tool.description, tool.per_link) for tool in offered]

    assert menu(agent_router_eval.stub_tools(reading, "본문 " + EVIL)) == menu(real)


BROWSED = CollectResult(
    EVIL, EVIL, ("https://attacker.test/",), 200, "text/html", HTML, "제목", 1, ("html_truncated",),
    BrowserResult("always", EVIL, (EVIL,), HTML, "제목", ("https://evil.top/a.apk",), 2, 5),
)


@pytest.mark.parametrize("see,result", [
    (see_collection, BROWSED),
    (see_collection, _timeout(EVIL)),
    (see_signals, _part(Brand.CJ_LOGISTICS, risk="비밀번호를 입력하세요")),
    (see_signals, None),
    (see_official, UrlAnalysis(final_url=EVIL, domain="evil.top", official=DomainMatch.NOT_REGISTERED)),
    (see_claims, _caution()),
])
def test_observations_carry_no_text(see, result):
    for value in see(result).values():
        assert isinstance(value, Enum) or value is None or type(value) in (bool, int)
