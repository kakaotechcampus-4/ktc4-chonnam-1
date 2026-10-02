"""RAG 사례 검색 평가 지표.

입출력 없는 순수 함수만 둔다. 점수는 사례와 문자의 유사성이며 스미싱 확률이
아니다. 기준값 미만 사례는 LLM 에 가지 않으므로 없는 것으로 친다. 점수는
search.py 가 소수 넷째 자리로 반올림해 보고한다.
"""

import hashlib
from collections.abc import Iterable, Sequence

from ai.types import CaseMatch

Matches = Sequence[CaseMatch]
SCORE_STEP = 0.0001


def split_key(row: dict) -> str:
    # 합성 정상 문자는 같은 템플릿끼리 거의 중복이다. 템플릿 단위로 묶어야
    # dev 와 test 에 같은 문제가 갈라져 들어가지 않는다.
    return row.get("template") or row["id"]


def is_dev(row: dict) -> bool:
    return hashlib.sha256(split_key(row).encode("utf-8")).digest()[0] % 2 == 0


def top1(matches: Matches) -> float:
    return matches[0].similarity if matches else 0.0


def attached(matches: Matches, threshold: float) -> bool:
    return bool(matches) and matches[0].similarity >= threshold


def first_hit_rank(
    matches: Matches, category: str, threshold: float, k: int = 3
) -> int | None:
    """기준값 이상이면서 정답 유형인 첫 사례의 순위. matches 는 점수 내림차순이다."""
    for rank, match in enumerate(matches[:k], start=1):
        if match.similarity < threshold:
            return None
        if category in match.categories:
            return rank
    return None


def hit_rate(ranks: Sequence[int | None], k: int) -> float:
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def mrr(ranks: Sequence[int | None]) -> float:
    return sum(1 / rank for rank in ranks if rank is not None) / len(ranks)


def attach_rate(match_lists: Sequence[Matches], threshold: float) -> float:
    return sum(attached(matches, threshold) for matches in match_lists) / len(match_lists)


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """positives 점수가 negatives 점수보다 클 확률. 동점은 0.5로 센다."""
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives
    )
    return wins / (len(positives) * len(negatives))


def pick_threshold(
    constrained: Sequence[Matches], max_rate: float, candidates: Iterable[float]
) -> float:
    """constrained 부착률이 max_rate 이하가 되는 가장 낮은 후보를 고른다.

    모든 후보가 넘으면 constrained top-1 최댓값보다 한 단계(0.0001) 위를 고른다.
    """
    for value in sorted(set(candidates)):
        if attach_rate(constrained, value) <= max_rate:
            return value
    return round(max(top1(matches) for matches in constrained) + SCORE_STEP, 4)
