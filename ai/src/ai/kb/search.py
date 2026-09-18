"""피해 사례 검색.

유사도는 사례와 메시지의 유사성입니다. 스미싱 확률이 아니며 판정을
바꾸지 않습니다. 검색 실패와 자료 부족을 유사도 0으로 대체하지 않습니다.
"""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ai.kb.normalize import normalize
from ai.types import AnalysisStatus, CaseMatch, CaseSearchResult, CategoryCode

LOGGER = logging.getLogger(__name__)

CASES_DIR = Path(__file__).resolve().parent / "case_examples"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)


@dataclass(frozen=True)
class Case:
    case_id: str
    variants: tuple[str, ...]
    normalized: tuple[str, ...]
    categories: tuple[CategoryCode, ...]


def _trigrams(text: str) -> set[str]:
    if len(text) < 3:
        return {text} if text else set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


def _similarity(left: set[str], right: set[str]) -> float:
    # ponytail: Jaccard는 길이 차에 민감하다. 짧은 정상 알림과 짧은 스미싱이
    # 겹칠 수 있다. eval에서 분포가 겹치면 containment나 임베딩으로 올린다.
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


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


@lru_cache(maxsize=8)
def load_cases(cases_dir: Path = CASES_DIR) -> tuple[Case, ...]:
    """status가 curated인 레코드만 인덱싱합니다.

    KB 296건을 요청마다 다시 파싱하면 0.15초가 든다. 프로세스 수명 동안
    캐시한다. KB 를 고치면 프로세스를 다시 띄워야 반영된다.
    """
    if not cases_dir.is_dir():
        return ()

    cases = []
    for path in sorted(cases_dir.glob("*.md")):
        case = _parse_case(path)
        if case is not None:
            cases.append(case)
    return tuple(cases)


def search_cases(
    masked_text: str,
    *,
    cases_dir: Path = CASES_DIR,
    top_k: int = 3,
    min_similarity: float = 0.3,
) -> CaseSearchResult:
    """링크를 제외하고 마스킹한 본문으로 검색합니다."""
    if not masked_text.strip():
        return CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[])

    cases = load_cases(cases_dir)
    if not cases:
        return CaseSearchResult(status=AnalysisStatus.FALLBACK, matches=[])

    query = _trigrams(normalize(masked_text))

    matches = []
    for case in cases:
        best_score = 0.0
        best_variant = case.variants[0]
        for variant, normalized_variant in zip(case.variants, case.normalized):
            score = _similarity(query, _trigrams(normalized_variant))
            if score > best_score:
                best_score = score
                best_variant = variant
        if best_score >= min_similarity:
            matches.append(
                CaseMatch(
                    case_id=case.case_id,
                    similarity=round(best_score, 4),
                    matched_variant=best_variant,
                    categories=list(case.categories),
                )
            )

    matches.sort(key=lambda item: item.similarity, reverse=True)
    return CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=matches[:top_k])
