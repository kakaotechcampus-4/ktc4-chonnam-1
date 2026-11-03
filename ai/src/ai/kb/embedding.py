"""사례 검색용 문장 임베딩.

엘리스 ML API 의 OpenAI 호환 `/v1/embeddings` 를 부른다. 키는 `LLM_API_KEY` 를 같이 쓰고,
`EMBEDDING_BASE_URL` 은 STT 처럼 `/v1` 없이 엔드포인트 루트를 넣는다. 재시도하지 않는다(ADR 0004).
임베딩이 실패하면 부르는 쪽이 문자 n-gram 검색만으로 폴백한다.
"""

import math
import os
from collections.abc import Sequence

import httpx

from ai.llm._client import required_env

DEFAULT_MODEL = "text-embedding-3-large"
TIMEOUT_SECONDS = 1.0
# 한 요청에 넣는 문장 수. KB 를 미리 임베딩할 때만 여러 묶음이 된다.
BATCH_SIZE = 64

Vector = tuple[float, ...]


def resolve_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return bool(os.getenv("EMBEDDING_BASE_URL", "").strip() and os.getenv("LLM_API_KEY", "").strip())


def unit(values: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return tuple(0.0 for _ in values)
    return tuple(value / norm for value in values)


def _parse(body: object, expected: int) -> list[Vector]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("예상 밖 임베딩 응답 형식")
    items = sorted(body["data"], key=lambda item: item.get("index", 0))
    if len(items) != expected:
        raise ValueError("임베딩 개수가 요청과 다름")
    return [unit(item["embedding"]) for item in items]


def embed_texts(
    texts: Sequence[str],
    *,
    timeout_s: float = TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> list[Vector]:
    """단위 벡터로 돌려준다. HTTP 오류는 httpx 예외, 형식 오류는 ValueError 로 올린다."""
    if not texts:
        return []
    url = required_env("EMBEDDING_BASE_URL").rstrip("/") + "/v1/embeddings"
    headers = {"Authorization": f"Bearer {required_env('LLM_API_KEY')}"}
    model = resolve_model()

    def _post(http: httpx.Client) -> list[Vector]:
        vectors: list[Vector] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = list(texts[start : start + BATCH_SIZE])
            response = http.post(url, headers=headers, json={"model": model, "input": batch})
            response.raise_for_status()
            vectors += _parse(response.json(), len(batch))
        return vectors

    if client is not None:
        return _post(client)
    with httpx.Client(timeout=timeout_s) as http:
        return _post(http)


def cosine(left: Vector, right: Vector) -> float:
    return sum(a * b for a, b in zip(left, right))
