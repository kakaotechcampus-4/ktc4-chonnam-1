"""피해 사례 검색.

유사도는 사례와 메시지의 유사성입니다. 스미싱 확률이 아니며 판정을
바꾸지 않습니다. 검색 실패와 자료 부족을 유사도 0으로 대체하지 않습니다.
"""

import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ai.kb.normalize import normalize
from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult, CategoryCode

LOGGER = logging.getLogger(__name__)

CASES_DIR = Path(__file__).resolve().parent / "case_examples"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_LOAD_LOCK = threading.Lock()

# ai/eval/rag_eval.py 의 dev 절반으로 정하는 값입니다. 바꾸면 평가를 다시 돌립니다.
NGRAM_SIZES: tuple[int, ...] = (3,)
SUBLINEAR_TF = False
DEFAULT_MIN_SIMILARITY = 0.2039


@dataclass(frozen=True)
class Case:
    case_id: str
    variants: tuple[str, ...]
    normalized: tuple[str, ...]
    categories: tuple[CategoryCode, ...]


@dataclass(frozen=True)
class CaseIndex:
    """문자 n-gram TF-IDF 색인입니다. variant 하나를 문서 하나로 칩니다."""

    cases: tuple[Case, ...]
    vectors: tuple[tuple[dict[str, float], ...], ...]
    idf: dict[str, float]
    unseen_idf: float
    ngram_sizes: tuple[int, ...]
    sublinear_tf: bool


def _ngrams(text: str, sizes: tuple[int, ...]) -> Counter[str]:
    grams = Counter(text[i : i + n] for n in sizes for i in range(len(text) - n + 1))
    if not grams and text:
        # 가장 짧은 n 보다 짧은 본문도 자기 자신으로 비교합니다.
        grams[text] += 1
    return grams


def _unit_vector(
    grams: Counter[str], idf: dict[str, float], unseen_idf: float, sublinear_tf: bool
) -> dict[str, float]:
    weights = {
        gram: (1 + math.log(count) if sublinear_tf else count) * idf.get(gram, unseen_idf)
        for gram, count in grams.items()
    }
    norm = math.sqrt(sum(weight * weight for weight in weights.values()))
    if norm == 0:
        return {}
    return {gram: weight / norm for gram, weight in weights.items()}


def build_index(
    cases: Iterable[Case],
    *,
    ngram_sizes: tuple[int, ...] = NGRAM_SIZES,
    sublinear_tf: bool = SUBLINEAR_TF,
) -> CaseIndex:
    cases = tuple(cases)
    grams = [[_ngrams(text, ngram_sizes) for text in case.normalized] for case in cases]
    documents = [doc for per_case in grams for doc in per_case]
    df = Counter(gram for doc in documents for gram in doc)
    total = len(documents)
    idf = {gram: math.log((1 + total) / (1 + count)) + 1 for gram, count in df.items()}
    # KB 에 없는 n-gram 도 질의 벡터 크기에 넣습니다. 질의 대부분이 KB 에 없는
    # 표현이면 점수가 낮아져 정상 문자에 사례가 덜 붙습니다.
    unseen_idf = math.log(1 + total) + 1
    vectors = tuple(
        tuple(_unit_vector(doc, idf, unseen_idf, sublinear_tf) for doc in per_case)
        for per_case in grams
    )
    return CaseIndex(cases, vectors, idf, unseen_idf, tuple(ngram_sizes), sublinear_tf)


def rank(index: CaseIndex, masked_text: str) -> list[CaseMatch]:
    """모든 사례를 코사인 유사도 내림차순으로 돌려줍니다. 동점은 KB 순서를 따릅니다."""
    query = _unit_vector(
        _ngrams(normalize(masked_text), index.ngram_sizes),
        index.idf,
        index.unseen_idf,
        index.sublinear_tf,
    )
    matches = []
    for case, variant_vectors in zip(index.cases, index.vectors):
        best_score = 0.0
        best_variant = case.variants[0]
        for variant, vector in zip(case.variants, variant_vectors):
            score = sum(weight * vector.get(gram, 0.0) for gram, weight in query.items())
            if score > best_score:
                best_score = score
                best_variant = variant
        matches.append(
            CaseMatch(
                case_id=case.case_id,
                similarity=round(min(best_score, 1.0), 4),
                matched_variant=best_variant,
                categories=list(case.categories),
            )
        )
    matches.sort(key=lambda item: item.similarity, reverse=True)
    return matches


def _parse_case(path: Path) -> Case | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("case read failed: %s", type(exc).__name__)
        return None

    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        LOGGER.warning("case parse failed: %s", type(exc).__name__)
        return None

    if not isinstance(meta, dict) or meta.get("status") != "curated":
        return None

    variants = [str(item) for item in meta.get("variants") or [] if str(item).strip()]
    if not variants:
        return None

    categories = []
    for code in meta.get("categories") or []:
        try:
            categories.append(CategoryCode(code))
        except ValueError:
            continue

    return Case(
        case_id=str(meta.get("id") or path.stem),
        variants=tuple(variants),
        normalized=tuple(normalize(item) for item in variants),
        categories=tuple(categories),
    )


def load_cases(cases_dir: Path = CASES_DIR) -> tuple[Case, ...]:
    """status가 curated인 레코드만 인덱싱합니다.

    KB 를 요청마다 다시 파싱하면 170~290ms 가 든다(측정). 프로세스 수명 동안
    캐시한다. KB 를 고치면 프로세스를 다시 띄워야 반영된다.

    lru_cache 만으로는 동시에 들어온 첫 호출들이 각자 KB 를 파싱한다. 기동 직후
    5건이 동시에 오면 GIL 경합으로 전부 1초 검색 예산을 넘겼다(측정). 락으로
    한 번만 파싱하고, 경로를 위치 인자로 넘겨 기본값 호출과 캐시를 공유한다.
    """
    with _LOAD_LOCK:
        return _load_cases(Path(cases_dir))


@lru_cache(maxsize=8)
def _load_cases(cases_dir: Path) -> tuple[Case, ...]:
    if not cases_dir.is_dir():
        return ()

    cases = []
    for path in sorted(cases_dir.glob("*.md")):
        case = _parse_case(path)
        if case is not None:
            cases.append(case)
    return tuple(cases)


def load_index(cases_dir: Path = CASES_DIR) -> CaseIndex:
    """색인도 프로세스 수명 동안 캐시합니다. load_cases 와 같은 락을 씁니다."""
    with _LOAD_LOCK:
        return _load_index(Path(cases_dir))


@lru_cache(maxsize=8)
def _load_index(cases_dir: Path) -> CaseIndex:
    return build_index(_load_cases(cases_dir))


def search_cases(
    masked_text: str,
    *,
    cases_dir: Path = CASES_DIR,
    top_k: int = 3,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> CaseSearchResult:
    """링크를 제외하고 마스킹한 본문으로 검색합니다.

    기준값은 보고하는 유사도(소수 넷째 자리 반올림)와 비교합니다. 평가 도구가
    고른 기준값이 운영에서도 같은 사례를 남기게 하기 위해서입니다.
    """
    if not masked_text.strip():
        return CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[])

    index = load_index(cases_dir)
    if not index.cases:
        return CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[])

    matches = [match for match in rank(index, masked_text) if match.similarity >= min_similarity]
    return CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=matches[:top_k])
