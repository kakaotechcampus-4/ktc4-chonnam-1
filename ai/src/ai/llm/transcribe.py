"""녹음 바이트를 whisper 로 받아쓴다 (스펙 D1).

받아쓰기만 한다. 재시도하지 않는다(ADR 0004). 오디오 변환도 하지 않는다 —
whisper 가 m4a·mp3·wav 등을 그대로 받는다. 예산은 부르는 쪽이 정한다.
"""

import mimetypes
import os

import httpx

from ai.llm._client import required_env

DEFAULT_MODEL = "whisper-large-v3"
TIMEOUT_SECONDS = 30.0


def resolve_model() -> str:
    return os.getenv("STT_MODEL", "").strip() or DEFAULT_MODEL


def _extract_text(body: object) -> str:
    # 실제 응답은 {"text": …}, 엘리스 문서 예시는 {"transcript": {"text": …}}
    if isinstance(body, dict):
        text = body.get("text")
        if text is None and isinstance(body.get("transcript"), dict):
            text = body["transcript"].get("text")
        # 무음이면 빈 문자열이 오는데 정상이다. `or` 로 넘기지 않는다
        if isinstance(text, str):
            return text.strip()
    raise ValueError("예상 밖 STT 응답 형식")


async def transcribe_audio(
    audio: bytes,
    filename: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = TIMEOUT_SECONDS,
) -> str:
    """HTTP 오류는 httpx 예외, 형식 오류는 ValueError 로 올린다."""
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    url = required_env("STT_BASE_URL").rstrip("/") + "/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {required_env('LLM_API_KEY')}",
        "accept": "application/json",
    }
    files = {"file": (filename, audio, mime)}
    data = {"model": resolve_model(), "language": "korean"}

    async def _post(http: httpx.AsyncClient) -> str:
        response = await http.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("STT 응답이 JSON 이 아님") from exc
        return _extract_text(body)

    if client is not None:
        return await _post(client)
    async with httpx.AsyncClient(timeout=timeout_s) as http:
        return await _post(http)
