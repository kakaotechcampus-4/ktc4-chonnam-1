"""서버 수명 동안의 준비·정리 작업을 한 곳에 묶는다."""

import time
from contextlib import asynccontextmanager

from ai.pipeline import warm_up_case_search
from ai.types import AnalysisStatus
from backend.src.server.urlscan_service import http_client_lifespan
from backend.src.server.shadow_tasks import shutdown_shadow_tasks


def warm_up_kb() -> None:
    """사례 검색 KB 를 요청이 오기 전에 불러온다.

    uvicorn 은 lifespan 시작이 끝난 뒤에 포트를 연다. 여기서 불러 두면 Render 가
    슬립에서 깨어난 직후의 첫 요청도 KB 로드를 1초 검색 예산 안에서 떠안지 않는다.
    실패해도 서버는 뜬다. 캐시는 예외를 저장하지 않으므로 첫 검색이 다시 시도한다.
    """
    start = time.monotonic()

    try:
        result = warm_up_case_search()
    except Exception as e:
        print(
            f"[KB WARMUP FAILED] "
            f"{type(e).__name__}: {e}"
        )
        return

    elapsed = time.monotonic() - start

    if result.status is not AnalysisStatus.COMPLETED:
        print(
            f"[KB WARMUP EMPTY] "
            f"status={result.status.value} "
            f"({elapsed:.2f}s)"
        )
        return

    print(f"[KB WARMUP] {elapsed:.2f}s")


@asynccontextmanager
async def app_lifespan(app):
    warm_up_kb()

    async with http_client_lifespan(app):
        try:
            yield
        finally:
            await shutdown_shadow_tasks()
