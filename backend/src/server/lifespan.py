"""서버 수명 동안의 준비·정리 작업을 한 곳에 묶는다."""

import time
from contextlib import asynccontextmanager

from ai.case import warm_up_voice_case_search
from ai.pipeline import warm_up_case_search
from ai.types import AnalysisStatus
from backend.src.server.http_client import http_client_lifespan


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


def warm_up_voice_case_search_task() -> None:
    """통화 사례 색인·사례 첨부 관문 모델을 요청이 오기 전에 불러온다.

    색인 콜드 적재가 약 1.4초라 호출하지 않으면 첫 통화 요청이 1초 사례
    검색 예산을 넘긴다(`latency-budget.md`). `warm_up_kb()`와 같은 이유로
    실패해도 서버는 뜬다 — 관문 모델 적재만 실패하면 사례가 안 붙을 뿐,
    `ai.case.voice.warm_up_voice_case_search()`가 이미 로그를 남긴다.
    """
    start = time.monotonic()

    try:
        result = warm_up_voice_case_search()
    except Exception as e:
        print(
            f"[VOICE KB WARMUP FAILED] "
            f"{type(e).__name__}: {e}"
        )
        return

    elapsed = time.monotonic() - start

    if result.status is not AnalysisStatus.COMPLETED:
        print(
            f"[VOICE KB WARMUP EMPTY] "
            f"status={result.status.value} "
            f"({elapsed:.2f}s)"
        )
        return

    print(f"[VOICE KB WARMUP] {elapsed:.2f}s")


@asynccontextmanager
async def app_lifespan(app):
    warm_up_kb()
    warm_up_voice_case_search_task()

    async with http_client_lifespan(app):
        yield
