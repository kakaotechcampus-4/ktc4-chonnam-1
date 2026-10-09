import asyncio

SHADOW_TASKS: set[asyncio.Task] = set()


def track_shadow_task(task: asyncio.Task) -> None:
    """Shadow 작업의 참조를 유지하고 완료 시 정리한다."""
    SHADOW_TASKS.add(task)

    def on_done(completed: asyncio.Task) -> None:
        SHADOW_TASKS.discard(completed)

        if completed.cancelled():
            return

        try:
            completed.result()
        except Exception as e:
            print(
                f"[SHADOW TASK ERROR] "
                f"{type(e).__name__}: {e}"
            )

    task.add_done_callback(on_done)


async def shutdown_shadow_tasks() -> None:
    """서버 종료 시 남아 있는 Shadow 작업을 취소한다."""
    tasks = list(SHADOW_TASKS)

    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )