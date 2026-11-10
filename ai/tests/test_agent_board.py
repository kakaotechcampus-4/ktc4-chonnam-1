"""분석 장부: (도구, 링크)마다 한 번만 돌고, 누가 불러도 같은 결과를 받는다."""

import asyncio

import pytest

from ai.agent import Board, Tool


def counting_tool(name="look", *, per_link=False, result="봤다", error=None, hold=False):
    """돈 횟수와 끝났는지를 남기는 도구. hold 면 취소될 때까지 멈춰 있다."""
    seen = {"runs": 0, "stopped": False}

    async def run(board, link):
        seen["runs"] += 1
        try:
            if hold:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            if error is not None:
                raise error
            return result
        finally:
            seen["stopped"] = True

    return Tool(name, "설명", per_link, run, lambda value: {"ok": True}), seen


def steps(board):
    return [(step.tool, step.link, step.askers, step.status) for step in board.trace().steps]


async def test_two_askers_share_one_run():
    tool, seen = counting_tool()
    board = Board([tool], link_count=0)

    first, second = await asyncio.gather(board.get(tool, by="agent"), board.get(tool))

    assert first == second == "봤다"
    assert seen["runs"] == 1
    assert steps(board) == [("look", None, ("agent", "code"), "done")]
    assert board.trace().steps[0].elapsed_ms >= 0


async def test_each_link_runs_once():
    tool, seen = counting_tool(per_link=True)
    board = Board([tool], link_count=2)

    await board.get(tool, 1)
    await board.get(tool, 2)
    await board.get(tool, 1)

    assert seen["runs"] == 2
    assert steps(board) == [("look", 1, ("code",), "done"), ("look", 2, ("code",), "done")]


async def test_failure_is_shared_and_not_retried():
    tool, seen = counting_tool(error=RuntimeError("down"))
    board = Board([tool], link_count=0)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="down"):
            await board.get(tool)

    assert seen["runs"] == 1
    assert steps(board) == [("look", None, ("code",), "failed")]


async def test_asking_starts_the_analysis_before_anyone_waits():
    tool, seen = counting_tool(hold=True)
    board = Board([tool], link_count=0)

    board.get(tool, by="agent")

    assert steps(board) == [("look", None, ("agent",), "running")]
    await board.reap()


async def test_cancelled_waiter_leaves_the_analysis_running():
    release = asyncio.Event()

    async def run(board, link):
        await release.wait()
        return "끝"

    tool = Tool("slow", "설명", False, run, lambda value: {})
    board = Board([tool], link_count=0)
    waiter = asyncio.ensure_future(board.get(tool))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)

    release.set()

    assert await board.get(tool) == "끝"


async def test_reap_cancels_unfinished_work():
    done, _ = counting_tool("done")
    held, seen = counting_tool("held", hold=True)
    board = Board([done, held], link_count=0)
    await board.get(done)
    board.get(held)
    await asyncio.sleep(0)

    await board.reap()

    assert seen["stopped"]
    assert steps(board) == [("done", None, ("code",), "done"), ("held", None, ("code",), "cancelled")]


@pytest.mark.parametrize("per_link,link", [(True, None), (False, 1), (True, 0), (True, 3)])
async def test_link_number_must_fit_the_tool(per_link, link):
    tool, seen = counting_tool(per_link=per_link)
    board = Board([tool], link_count=2)

    with pytest.raises(ValueError):
        board.get(tool, link)

    assert seen["runs"] == 0
    assert steps(board) == []
