"""한 요청의 분석 장부.

분석은 (도구, 링크)마다 한 번만 돈다. 카드를 만드는 코드와 에이전트가 같은 장부에서 꺼내므로
한쪽이 시작한 분석을 다른 쪽이 다시 돌리지 않는다.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Literal, TypeVar

T = TypeVar("T")
By = Literal["code", "agent"]
Stop = Literal["off", "done", "turn_limit", "call_limit", "llm_error", "preempted"]
# LLM 이 보는 관측. 값은 bool·int·Enum·None 뿐이다. 원문·URL·HTML 이 지나갈 str 자리가 없다
Obs = Mapping[str, bool | int | Enum | None]


@dataclass(frozen=True)
class Tool(Generic[T]):
    name: str            # LLM 함수 이름
    description: str     # LLM 에게 보이는 고정 문구
    per_link: bool       # True: 인자는 링크 번호 하나. False: 인자 없음. 자유 문자열 인자는 없다
    # 판정 코드가 읽는 원본 결과. 다른 분석이 필요하면 board.get 으로 꺼낸다
    run: Callable[["Board", int | None], Awaitable[T]]
    observe: Callable[[T], Obs]


@dataclass(frozen=True)
class Step:
    tool: str
    link: int | None
    askers: tuple[By, ...]  # 요청한 순서(중복 없음). 첫 번째가 실제로 시작한 쪽
    status: Literal["running", "done", "failed", "cancelled"]
    elapsed_ms: int | None


@dataclass(frozen=True)
class Trace:
    steps: tuple[Step, ...]
    turns: int  # 에이전트 LLM 호출 수
    stop: Stop


@dataclass
class _Entry:
    task: asyncio.Task
    askers: list[By]
    elapsed_ms: int | None = None


def _status(task: asyncio.Task) -> str:
    if not task.done():
        return "running"
    if task.cancelled():
        return "cancelled"
    return "failed" if task.exception() else "done"


class Board:
    """(도구, 링크)마다 Task 하나. 누가 불러도 같은 결과·같은 예외를 받는다."""

    def __init__(self, tools: Sequence[Tool], link_count: int) -> None:
        self.tools = tuple(tools)
        self.link_count = link_count
        # 에이전트 루프가 적는다
        self.turns = 0
        self.stop: Stop = "off"
        self._entries: dict[tuple[Tool, int | None], _Entry] = {}

    def get(self, tool: Tool[T], link: int | None = None, *, by: By = "code") -> Awaitable[T]:
        """없으면 시작하고, 있으면 같은 Task 를 기다린다. 재시도하지 않는다.

        Task 는 부르는 즉시 등록된다. 기다리던 쪽이 취소돼도 분석은 계속된다(shield).
        링크 번호는 1부터다. per_link 와 맞지 않거나 범위 밖이면 ValueError.
        """
        if tool.per_link != (link is not None) or (link is not None and not 1 <= link <= self.link_count):
            raise ValueError(f"{tool.name}: link={link!r}")
        entry = self._entries.get((tool, link))
        if entry is None:
            entry = self._entries[tool, link] = _Entry(asyncio.create_task(self._run(tool, link)), [])
        if by not in entry.askers:
            entry.askers.append(by)
        return asyncio.shield(entry.task)

    async def _run(self, tool: Tool[T], link: int | None) -> T:
        started = time.monotonic()
        try:
            return await tool.run(self, link)
        finally:
            self._entries[tool, link].elapsed_ms = int((time.monotonic() - started) * 1000)

    async def reap(self) -> None:
        """끝나지 않은 분석을 모두 취소하고 끝날 때까지 기다린다."""
        tasks = [entry.task for entry in self._entries.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def trace(self) -> Trace:
        steps = tuple(
            Step(tool.name, link, tuple(entry.askers), _status(entry.task), entry.elapsed_ms)
            for (tool, link), entry in self._entries.items()
        )
        return Trace(steps, self.turns, self.stop)
