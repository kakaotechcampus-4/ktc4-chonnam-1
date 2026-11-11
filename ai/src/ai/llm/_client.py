"""LLM 호출 한 번의 공통 절차. 호출 예산은 부르는 쪽이 소유합니다.

예산을 이 파일에 고정하면 모듈별 `TIMEOUT_SECONDS` 선언이 조용히 무시됩니다.
단계별 값의 근거는 `docs/latency-budget.md`, 결정 기록은
`docs/adr/0004-llm-call-budget-ownership.md`.

호출 → 거절·빈 답 확인 → 스키마 검증(`parse_answer`)과 실패를 코드로 바꾸는 표(`failure_of`)를
여기 한 벌만 둔다. 단계마다 복사해 두면 클라이언트 정리와 실패 코드가 조금씩 어긋난다.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from openai import (
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

from ai.diagnostics import where
from ai.types import FailureCode

_T = TypeVar("_T", bound=BaseModel)
_DATA_CLOSE = re.compile(r"</(data)", re.IGNORECASE)


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


@lru_cache(maxsize=None)
def prompt_text(path: Path) -> str:
    """프롬프트 파일. 요청마다 디스크를 읽지 않도록 한 번만 읽는다."""
    return path.read_text(encoding="utf-8")


def data_line(text: str) -> str:
    """받은 글 한 줄을 data 태그로 감싼다. 글 안의 닫는 태그는 대소문자와 상관없이 무력화한다."""
    return "<data>" + _DATA_CLOSE.sub(r"< /\1", " ".join(text.split())) + "</data>"


class NoAnswer(Exception):
    """LLM 이 답을 거절했거나 스키마에 맞는 답을 내지 않았다."""

    def __init__(self, failure: FailureCode) -> None:
        super().__init__(failure.value)
        self.failure = failure


async def parse_answer(
    response_format: type[_T],
    *,
    system: str,
    user: str,
    client: AsyncOpenAI | None,
    make_client: Callable[[float], AsyncOpenAI],
    model: str,
    timeout_s: float,
) -> _T:
    """LLM 을 한 번 부르고 `response_format` 에 맞는 답을 돌려준다.

    `client` 를 받지 않으면 `make_client(timeout_s)` 로 만들어 쓰고 닫는다. 받은 것은 닫지 않는다.
    실패는 예외로 올린다. 부른 쪽이 `failure_of` 로 실패 코드로 바꿔 폴백 결과를 만든다.
    """
    async with AsyncExitStack() as stack:
        llm = client
        if llm is None:
            llm = await stack.enter_async_context(make_client(timeout_s))
        response = await asyncio.wait_for(
            llm.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_format,
                temperature=0,
            ),
            timeout=timeout_s,
        )
    message = response.choices[0].message
    if message.refusal:
        raise NoAnswer(FailureCode.REFUSED)
    if message.parsed is None:
        raise NoAnswer(FailureCode.INVALID_OUTPUT)
    return response_format.model_validate(message.parsed)


def failure_of(error: Exception, label: str, logger: logging.Logger) -> FailureCode:
    """LLM 단계에서 난 예외를 실패 코드로 바꾸고 원인을 `logger` 에 남긴다.

    로그에는 예외 종류, HTTP 상태 코드, 깨진 칸 이름만 남긴다. 예외 문구와 값에는 문자 내용이나
    제공자가 보낸 정보가 들어 있어 남기지 않는다.
    """
    if isinstance(error, NoAnswer):
        logger.warning("%s failed: %s", label, error.failure.value)
        return error.failure
    if isinstance(error, (TimeoutError, APITimeoutError)):
        logger.warning("%s failed: %s", label, type(error).__name__)
        return FailureCode.TIMEOUT
    if isinstance(error, ValidationError):
        fields = ",".join(
            ".".join(str(part) for part in item["loc"]) + ":" + item["type"]
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        )
        logger.warning("%s failed: %s fields=%s", label, type(error).__name__, fields)
        return FailureCode.INVALID_OUTPUT
    if isinstance(error, (APIResponseValidationError, LengthFinishReasonError, json.JSONDecodeError)):
        logger.warning("%s failed: %s", label, type(error).__name__)
        return FailureCode.INVALID_OUTPUT
    if isinstance(error, APIStatusError):
        logger.warning("%s failed: %s status=%s", label, type(error).__name__, error.status_code)
        return FailureCode.LLM_ERROR
    logger.warning("%s failed: %s", label, where(error))
    return FailureCode.LLM_ERROR
