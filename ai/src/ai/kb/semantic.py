"""사례 검색의 임베딩 쪽 점수.

KB 사례 벡터는 기동 때(warm-up) 한 번 만들어 프로세스 수명 동안 쓴다. 질의 벡터는 요청마다 부른다.
최종 점수는 `alpha * 문자 n-gram TF-IDF + (1 - alpha) * 임베딩 코사인` 이다(ai/eval/embed_rag_experiment.py,
embed_voice_experiment.py 의 dev 로 정했다). 임베딩이 설정되지 않았거나 실패하면 None 을 돌려주고, 부르는 쪽이
TF-IDF 만으로 검색한다. 유사도는 사례와 질의의 유사성이며 판정에 쓰지 않는다.
"""

import logging
import math
import operator
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from ai.kb import embedding
from ai.kb.embedding import Vector

LOGGER = logging.getLogger(__name__)

# 질의 임베딩 한도. 검색 예산(문자 1초) 안에서 TF-IDF 폴백까지 마치도록 남긴다. 측정: 연결 재사용 시 p50 약 0.3초.
QUERY_TIMEOUT_S = 0.6
# KB 를 처음 임베딩할 때의 한도. 기동 때만 돈다.
BUILD_TIMEOUT_S = 60.0
# KB 임베딩이 실패하면 이 시간 동안 다시 시도하지 않는다. 그동안은 TF-IDF 만 쓴다.
RETRY_AFTER_S = 300.0
# 통화 대본은 모델 입력 한도를 넘으므로 이 글자 수 조각으로 나눠 평균한다(평가와 같은 값).
VOICE_CHUNK_CHARS = 1500

_dot: Callable[[Vector, Vector], float] = getattr(math, "sumprod", None) or (
    lambda left, right: sum(map(operator.mul, left, right))
)


@dataclass(frozen=True)
class EmbeddingIndex:
    """사례 id 마다 벡터 묶음. 문자는 variant 마다 하나, 통화는 조각 평균 하나다."""

    vectors: dict[str, tuple[Vector, ...]]


def chunk(text: str, size: int) -> list[str]:
    """어절을 끊지 않고 size 자 남짓의 조각으로 나눈다. 조각은 절반씩 겹친다(평가와 같은 규칙)."""
    words = text.split()
    pieces, start = [], 0
    while start < len(words):
        end, length = start, 0
        while end < len(words) and length < size:
            length += len(words[end]) + 1
            end += 1
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        step, length = start, 0
        while step < end and length < size // 2:
            length += len(words[step]) + 1
            step += 1
        start = max(step, start + 1)
    return pieces or [text[:size]]


def mean_vector(vectors: Sequence[Vector]) -> Vector:
    return embedding.unit([sum(column) / len(vectors) for column in zip(*vectors)])


_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    # 요청마다 연결을 새로 열면 TLS 때문에 0.5~1초가 든다(측정). 연결을 재사용한다.
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=QUERY_TIMEOUT_S)
        return _client


def embed_query(text: str) -> Vector:
    return embedding.embed_texts([text], client=_http())[0]


def embed_transcript(text: str) -> Vector:
    return mean_vector(embedding.embed_texts(chunk(text, VOICE_CHUNK_CHARS), client=_http()))


_index_lock = threading.Lock()
_indexes: dict[tuple[Path, str], EmbeddingIndex] = {}
_failed_at: dict[tuple[Path, str], float] = {}


def _build(cases, kind: str) -> EmbeddingIndex:
    with httpx.Client(timeout=BUILD_TIMEOUT_S) as http:
        if kind == "voice":
            pieces = [
                [piece for variant in case.variants for piece in chunk(variant, VOICE_CHUNK_CHARS)] for case in cases
            ]
            flat = iter(embedding.embed_texts([piece for group in pieces for piece in group], client=http))
            return EmbeddingIndex(
                {case.case_id: (mean_vector([next(flat) for _ in group]),) for case, group in zip(cases, pieces)}
            )
        texts = [variant for case in cases for variant in case.variants]
        flat = iter(embedding.embed_texts(texts, client=http))
        return EmbeddingIndex({case.case_id: tuple(next(flat) for _ in case.variants) for case in cases})


def load_index(cases_dir: Path, kind: str, cases) -> EmbeddingIndex | None:
    """KB 벡터. 설정이 없거나 만들다 실패하면 None 이다(RETRY_AFTER_S 뒤에 다시 시도)."""
    if not embedding.is_configured() or not cases:
        return None
    key = (Path(cases_dir), kind)
    with _index_lock:
        if key in _indexes:
            return _indexes[key]
        if time.monotonic() - _failed_at.get(key, -RETRY_AFTER_S) < RETRY_AFTER_S:
            return None
        try:
            _indexes[key] = _build(cases, kind)
        except Exception as exc:
            LOGGER.warning("case embedding build failed: %s", type(exc).__name__)
            _failed_at[key] = time.monotonic()
            return None
        return _indexes[key]


def scores(index: EmbeddingIndex, query: Vector) -> dict[str, float]:
    """사례마다 질의와 가장 가까운 벡터의 코사인."""
    return {case_id: max(_dot(query, vector) for vector in vectors) for case_id, vectors in index.vectors.items()}


def reset() -> None:
    """테스트용. 만든 색인과 실패 기록을 지운다."""
    with _index_lock:
        _indexes.clear()
        _failed_at.clear()
