"""요청 하나의 조사: 분석 장부를 열고 닫는다."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from ai.agent.board import Board, Tool
from ai.types import MessageReading


@asynccontextmanager
async def investigation(reading: MessageReading, tools: Sequence[Tool]) -> AsyncIterator[Board]:
    """Board 를 연다. 나갈 때(정상·예외·취소 모두) 남은 분석을 취소하고 회수한다."""
    board = Board(tools, len(reading.links))
    try:
        yield board
    finally:
        await board.reap()
