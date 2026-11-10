"""요청 하나의 조사: 분석 장부를 열고, 돌릴 분석을 고르는 에이전트 루프를 붙인다.

에이전트는 도구를 고르기만 한다. 판정하지 않고, 모델이 쓴 글은 읽지 않는다. 카드가 읽는 분석은
카드 코드가 board 에서 꺼내므로 에이전트가 빼먹거나 실패해도 돈다.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from enum import Enum
from pathlib import Path

from openai import AsyncOpenAI

from ai.agent.board import Board, Stop, Tool
from ai.case.privacy import mask_pii
from ai.diagnostics import where
from ai.llm._client import create_client, prompt_text
from ai.types import MessageReading

LOGGER = logging.getLogger(__name__)

MAX_TURNS = 4          # LLM 호출 수
MAX_CALLS = 5          # 에이전트가 실행시킨 도구 호출 수
TURN_TIMEOUT_S = 5.0   # LLM 한 턴
GRACE_S = 3.0          # 카드가 다 만들어진 뒤 에이전트가 끝나기를 기다려 주는 시간
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "agent_router.md"


@asynccontextmanager
async def investigation(
    reading: MessageReading,
    tools: Sequence[Tool],
    *,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
    grace_until: float | None = None,
) -> AsyncIterator[Board]:
    """Board 를 열고 에이전트 루프를 붙인다. 나갈 때 루프와 남은 분석을 모두 취소하고 회수한다.

    에이전트가 먼저 고른다. 첫 턴의 도구 호출이 board 에 오르거나 루프가 끝난 뒤에 board 를 내준다.
    모델은 인자 → AGENT_MODEL 이고, 없으면 루프를 돌리지 않는다. 에이전트의 실패는 밖으로 나오지 않는다.
    `grace_until`(time.monotonic 기준)을 넘겨서는 에이전트를 기다리지 않는다. 다 만든 카드를
    부른 쪽의 전체 상한에 잃지 않게 하려는 것이다.
    """
    board = Board(tools, len(reading.links))
    model = model or os.getenv("AGENT_MODEL", "").strip()
    agent = None
    try:
        if model:
            moved = asyncio.Event()
            agent = asyncio.create_task(_run_agent(board, reading, client, model, moved))
            await moved.wait()
        yield board
        if agent is not None:
            # 본문이 정상으로 끝났을 때만 온다. 바로 끊으면 기록이 늘 preempted 로 남는다
            grace = GRACE_S if grace_until is None else min(GRACE_S, grace_until - time.monotonic())
            if grace > 0:
                await asyncio.wait({agent}, timeout=grace)
    finally:
        if agent is not None:
            agent.cancel()
            await asyncio.gather(agent, return_exceptions=True)
        await board.reap()


async def _run_agent(
    board: Board, reading: MessageReading, client: AsyncOpenAI | None, model: str, moved: asyncio.Event,
) -> None:
    """루프를 돌리고 끝난 이유를 board 에 적는다. 취소 말고는 예외를 올리지 않는다."""
    try:
        async with AsyncExitStack() as stack:
            llm = client or await stack.enter_async_context(create_client(TURN_TIMEOUT_S))
            board.stop = await _loop(board, _brief(reading), llm, model, moved)
    except asyncio.CancelledError:
        board.stop = "preempted"
        raise
    except Exception as error:  # noqa: BLE001 — 에이전트가 실패해도 카드 코드는 그대로 돈다 (원칙 3)
        LOGGER.warning("agent loop failed: %s", where(error))
        board.stop = "llm_error"
    finally:
        moved.set()


async def _loop(board: Board, brief: str, llm: AsyncOpenAI, model: str, moved: asyncio.Event) -> Stop:
    messages: list[dict] = [
        {"role": "system", "content": prompt_text(PROMPT_PATH)},
        {"role": "user", "content": brief},
    ]
    schemas = _schemas(board)
    calls_left = MAX_CALLS
    for _ in range(MAX_TURNS):
        board.turns += 1
        reply = await asyncio.wait_for(
            llm.chat.completions.create(
                model=model, messages=messages, tools=schemas, tool_choice="auto", temperature=0,
            ),
            timeout=TURN_TIMEOUT_S,
        )
        calls = reply.choices[0].message.tool_calls or []
        if not calls:
            return "done"
        targets = [_parse_call(call.function.name, call.function.arguments, board) for call in calls]
        waiting = {}
        for index, target in enumerate(targets):
            if isinstance(target, str):
                continue
            if not calls_left:
                targets[index] = "call_limit"
                continue
            calls_left -= 1
            waiting[index] = board.get(*target, by="agent")
        # 이 턴의 호출이 모두 board 에 올랐다. 첫 턴이면 기다리던 카드 코드가 여기서 출발한다
        moved.set()
        results = dict(zip(waiting, await asyncio.gather(*waiting.values(), return_exceptions=True)))
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": call.id, "type": "function",
             "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in calls
        ]})
        for index, (call, target) in enumerate(zip(calls, targets)):
            shown = {"error": target} if isinstance(target, str) else _show(target[0], results[index])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(shown)})
        if not calls_left:
            return "call_limit"
    return "turn_limit"


def _brief(reading: MessageReading) -> str:
    """LLM 이 받는 입력. 링크 수와, 개인정보를 가린 본문을 줄마다 data 태그로 감싼 것이다.

    본문은 링크 구간을 뗀 것이라 주소가 없다. 원문 안의 닫는 태그는 무력화한다(llm/claims.py 와 같다).
    """
    lines = [
        f"<data>{' '.join(line.split()).replace('</data', '< /data')}</data>"
        for line in mask_pii(reading.message).splitlines() if line.strip()
    ]
    return "\n".join([f"링크 수: {len(reading.links)}", *lines])


def _schemas(board: Board) -> list[dict]:
    """도구 스키마. 인자는 링크 번호(문자에 있는 링크 수까지)뿐이라 주소·글자를 넘길 자리가 없다."""
    links = list(range(1, board.link_count + 1))
    schemas = []
    for tool in board.tools:
        if tool.per_link and not links:
            continue
        properties = {"link": {"type": "integer", "enum": links}} if tool.per_link else {}
        schemas.append({"type": "function", "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False,
            },
        }})
    return schemas


def _parse_call(name: str, arguments: str, board: Board) -> tuple[Tool, int | None] | str:
    """LLM 의 호출을 (도구, 링크 번호)로 읽는다. 받을 수 없으면 오류 코드를 돌려준다.

    게이트웨이가 스키마를 강제한다고 믿지 않는다. 모르는 이름, 여분의 인자, 범위 밖 번호는 실행하지 않는다.
    """
    tool = next((tool for tool in board.tools if tool.name == name), None)
    if tool is None:
        return "unknown_tool"
    try:
        args = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return "bad_arguments"
    if not tool.per_link:
        return (tool, None) if args == {} else "bad_arguments"
    link = args.get("link") if isinstance(args, dict) and set(args) == {"link"} else None
    # JSON true 는 파이썬에서 int 로도 읽히므로 형식을 그대로 본다
    if type(link) is not int or not 1 <= link <= board.link_count:
        return "bad_arguments"
    return tool, link


def _show(tool: Tool, result: object) -> dict:
    """도구 결과가 LLM 으로 가는 유일한 통로. 관측 값은 bool·int·Enum·None 만 지나간다.

    글자(str)가 하나라도 있으면 관측을 통째로 버린다. 원문·주소·HTML 이 실려 나갈 수 없다.
    """
    if isinstance(result, BaseException):
        return {"error": "failed"}
    shown = {}
    for key, value in tool.observe(result).items():
        if isinstance(value, Enum):
            value = value.value
        elif value is not None and type(value) not in (bool, int):
            return {"error": "observation_rejected"}
        shown[key] = value
    return shown
