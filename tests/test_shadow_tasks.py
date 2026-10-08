import asyncio

import pytest

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