import asyncio
import gc
import weakref

import pytest
import pytest_asyncio

from backend.src.server.shadow_tasks import (
    SHADOW_TASKS,
    shutdown_shadow_tasks,
    track_shadow_task,
)


@pytest.mark.asyncio
async def test_shadow_task_removed_after_completion():
    async def sample_task():
        return "completed"

    task = asyncio.create_task(sample_task())
    track_shadow_task(task)

    assert task in SHADOW_TASKS

    await task
    await asyncio.sleep(0)

    assert task not in SHADOW_TASKS


@pytest.mark.asyncio
async def test_shutdown_cancels_shadow_tasks():
    async def long_running_task():
        await asyncio.sleep(100)

    task = asyncio.create_task(long_running_task())
    track_shadow_task(task)

    await shutdown_shadow_tasks()

    assert task.cancelled()
    assert task not in SHADOW_TASKS

@pytest.mark.asyncio
async def test_shadow_collector_does_not_block_other_tasks():
    """Collector가 완료되지 않아도 다른 비동기 작업은 진행된다."""
    import main

    collector_started = asyncio.Event()
    release_collector = asyncio.Event()

    async def slow_collector():
        collector_started.set()
        await release_collector.wait()
        return None

    collector_task = asyncio.create_task(slow_collector())
    track_shadow_task(collector_task)

    shadow_task = asyncio.create_task(
        main.log_collector_shadow(
            collector_task=collector_task,
            link="https://example.com",
            parsed_result={
                "final_url": "https://example.com",
                "domain": "example.com",
            },
            urlscan_elapsed=0.1,
        )
    )
    track_shadow_task(shadow_task)

    try:
        await asyncio.wait_for(collector_started.wait(), timeout=1)

        # Collector는 아직 끝나지 않았다.
        assert not collector_task.done()

        # 다른 작업은 Collector 완료를 기다리지 않고 실행될 수 있다.
        async def other_analysis():
            return "analysis completed"

        result = await asyncio.wait_for(
            other_analysis(),
            timeout=1,
        )

        assert result == "analysis completed"
        assert not collector_task.done()

    finally:
        release_collector.set()
        await asyncio.gather(
            shadow_task,
            return_exceptions=True,
        )


@pytest_asyncio.fixture
async def shadow_loop_errors():
    """Collect loop diagnostics, and restore the handler after task cleanup."""
    await shutdown_shadow_tasks()
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        yield errors
    finally:
        await shutdown_shadow_tasks()
        loop.set_exception_handler(previous_handler)


def track_until_settled(task):
    """Observe completion after the production tracking callback has run."""
    settled = asyncio.Event()
    track_shadow_task(task)
    task.add_done_callback(lambda _task: settled.set())
    return settled


@pytest.mark.asyncio
async def test_failed_shadow_task_is_removed_and_exception_retrieved(shadow_loop_errors, capsys):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail():
        started.set()
        await release.wait()
        raise RuntimeError("shadow test failure")

    task = asyncio.create_task(fail())
    reference = weakref.ref(task)
    settled = track_until_settled(task)
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert task in SHADOW_TASKS
        release.set()
        await asyncio.wait_for(settled.wait(), timeout=1)
        assert task.done()
        assert task not in SHADOW_TASKS
        assert "[SHADOW TASK ERROR] RuntimeError: shadow test failure" in capsys.readouterr().out

        # Do not await task or call result()/exception(): that would retrieve
        # the exception in the test and hide a broken production callback.
        task = None
        gc.collect()
        assert reference() is None, "Task must be destroyed to exercise unretrieved-exception reporting"
        assert shadow_loop_errors == []  # Includes 'Task exception was never retrieved'.
        assert not SHADOW_TASKS
    finally:
        release.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_individually_cancelled_shadow_task_is_removed(shadow_loop_errors, capsys):
    started = asyncio.Event()
    stopped = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release():
        started.set()
        try:
            await release.wait()
        finally:
            stopped.set()

    task = asyncio.create_task(wait_for_release())
    settled = track_until_settled(task)
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert task in SHADOW_TASKS
        task.cancel()
        await asyncio.wait_for(settled.wait(), timeout=1)
        assert task.cancelled()
        assert stopped.is_set()
        assert task not in SHADOW_TASKS
        assert not SHADOW_TASKS
        assert "[SHADOW TASK ERROR]" not in capsys.readouterr().out
        assert shadow_loop_errors == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["failure", "cancellation"])
async def test_shadow_task_termination_does_not_affect_other_task(termination, shadow_loop_errors):
    failing_started = asyncio.Event()
    healthy_started = asyncio.Event()
    release_failing = asyncio.Event()
    release_healthy = asyncio.Event()

    async def interrupted():
        failing_started.set()
        await release_failing.wait()
        raise RuntimeError("independent shadow failure")

    async def healthy():
        healthy_started.set()
        await release_healthy.wait()
        return "healthy result"

    first = asyncio.create_task(interrupted())
    second = asyncio.create_task(healthy())
    first_settled = track_until_settled(first)
    second_settled = track_until_settled(second)
    try:
        await asyncio.wait_for(failing_started.wait(), timeout=1)
        await asyncio.wait_for(healthy_started.wait(), timeout=1)
        assert SHADOW_TASKS == {first, second}
        if termination == "failure":
            release_failing.set()
        else:
            first.cancel()
        await asyncio.wait_for(first_settled.wait(), timeout=1)
        assert first.done()
        assert first.cancelled() is (termination == "cancellation")
        assert SHADOW_TASKS == {second}
        assert not second.done()

        release_healthy.set()
        await asyncio.wait_for(second_settled.wait(), timeout=1)
        assert second.result() == "healthy result"
        assert not SHADOW_TASKS
        assert shadow_loop_errors == []
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
