"""받아쓴 문장마다 화자를 고른다 (스펙 D1).

문장은 코드가 자르고 LLM 은 번호마다 화자만 고른다. 그래서 LLM 이 문장을 고쳐 쓰거나
없는 말을 지어낼 수 없다. 판정은 하지 않는다. 재시도하지 않는다(ADR 0004).
"""

import json
import os
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ValidationError

from ai.llm._client import required_env
from ai.types import StrictModel

DEFAULT_MODEL = "claude-sonnet-5-5"
TIMEOUT_SECONDS = 20.0
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v1" / "diarize_call.md"
_CLOSE_TAG = "</받아쓴_글"


class SpeakerLabel(StrictModel):
    id: int
    speaker: Literal["화자1", "화자2", "기타"]


class Diarization(StrictModel):
    lines: list[SpeakerLabel]


def resolve_model() -> str:
    return os.getenv("DIARIZE_MODEL", "").strip() or DEFAULT_MODEL


def _render(sentences: list[str]) -> str:
    # 원문 안의 닫는 태그는 무력화한다
    rows = "\n".join(
        f"{i}. {s.replace(_CLOSE_TAG, '< /받아쓴_글')}" for i, s in enumerate(sentences)
    )
    return f"<받아쓴_글>\n{rows}\n</받아쓴_글>"


def _parse_sse(lines: list[str]) -> str:
    """content_block_delta 의 text_delta 를 이어 붙인다."""
    parts: list[str] = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "error":
            raise ValueError("화자 분리 스트림 오류")
        delta = event.get("delta")
        if (
            event.get("type") == "content_block_delta"
            and isinstance(delta, dict)
            and delta.get("type") == "text_delta"
        ):
            parts.append(str(delta.get("text", "")))
    return "".join(parts)


def parse_labels(text: str) -> dict[int, str]:
    """첫 `{` 부터 마지막 `}` 까지를 검증한다. 앞뒤 잡글은 허용한다."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("화자 분리 응답에 JSON 이 없음")
    try:
        parsed = Diarization.model_validate_json(text[start : end + 1])
    except ValidationError as exc:
        raise ValueError("화자 분리 응답 검증 실패") from exc
    labels: dict[int, str] = {}
    for line in parsed.lines:
        labels.setdefault(line.id, line.speaker)
    return labels


async def pick_speakers(
    sentences: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = TIMEOUT_SECONDS,
) -> dict[int, str]:
    """번호 → 화자. 빠진 번호는 채우지 않는다(부르는 쪽이 `기타` 로 둔다)."""
    url = required_env("DIARIZE_BASE_URL").rstrip("/") + "/v1/messages"
    headers = {
        "Authorization": f"Bearer {required_env('LLM_API_KEY')}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": resolve_model(),
        "max_tokens": 16000,
        "system": PROMPT_PATH.read_text(encoding="utf-8"),
        "messages": [{"role": "user", "content": _render(sentences)}],
        "output_config": {"effort": "medium"},
        # 엘리스 프록시는 스트리밍이 아니면 출력을 2,000 토큰으로 자른다
        "stream": True,
    }

    async def _call(http: httpx.AsyncClient) -> str:
        async with http.stream("POST", url, headers=headers, json=body) as response:
            response.raise_for_status()
            lines = [line async for line in response.aiter_lines()]
        return _parse_sse(lines)

    if client is not None:
        text = await _call(client)
    else:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            text = await _call(http)
    return parse_labels(text)
