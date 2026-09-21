"""LLM 클라이언트 생성. 호출 예산은 부르는 쪽이 소유합니다.

예산을 이 파일에 고정하면 모듈별 `TIMEOUT_SECONDS` 선언이 조용히 무시됩니다.
단계별 값의 근거는 `docs/latency-budget.md`, 결정 기록은
`docs/adr/0004-llm-call-budget-ownership.md`.
"""

import os

from openai import AsyncOpenAI


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def create_client(timeout: float) -> AsyncOpenAI:
    """`timeout` 은 호출 단계가 자기 예산에서 넘깁니다.

    재시도는 금지입니다. 한 번이라도 재시도하면 단계 예산이 배로 늘어나
    카카오 5초 SLA 를 넘깁니다.
    """
    return AsyncOpenAI(
        api_key=required_env("LLM_API_KEY"),
        base_url=required_env("LLM_BASE_URL"),
        timeout=timeout,
        max_retries=0,
    )
