# RAG 사례 검색 평가·개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rag_testset.jsonl` 로 사례 검색 품질을 재는 평가 도구를 만들고, KB 에 유형을 붙인 뒤 검색을 문자 n-gram TF-IDF 코사인으로 바꿔 dev 에서 정한 설정·기준값을 test 에서 한 번 확인한다.

**Architecture:** 평가 코드는 런타임 패키지 밖(`ai/eval/`)에 둔다. 지표는 순수 함수(`rag_metrics.py`), 실행·리포트는 `rag_eval.py` 가 맡는다. 검색은 `search.py` 안에서 색인 생성(`build_index`)과 순위(`rank`)를 공개 함수로 나누고, `search_cases()` 의 시그니처·반환 타입은 그대로 둔다. KB 유형 라벨은 별도 PR 로 올리고 머지 뒤에만 수치의 근거로 쓴다.

**Tech Stack:** Python ≥3.11 표준 라이브러리(hashlib, math, statistics, argparse, collections), 기존 pytest·Pydantic·PyYAML. 새 의존성 없음.

**Spec:** [RAG 사례 검색 평가·개선 설계](../specs/2026-10-02-rag-eval-improvement-design.md)

## Global Constraints

- 검색 결과는 판정을 바꾸지 않는다. `ai/src/ai/verdict.py`, `ai/src/ai/pipeline/analysis.py`, `ai/src/ai/llm/signals.py` 는 수정하지 않는다.
- 새 의존성을 추가하지 않는다. `ai/pyproject.toml` 은 수정하지 않는다.
- `ai/` 안에서 `server` 를 import 하지 않는다.
- `search_cases(masked_text, *, cases_dir, top_k, min_similarity) -> CaseSearchResult` 의 시그니처와 반환 타입을 유지한다. `CaseMatch.similarity` 는 0~1, 소수 넷째 자리 반올림이다.
- 사례 검색 예산은 1.0초다(`docs/latency-budget.md`). 리포트에 질의 p50·p95와 첫 호출 소요를 남긴다.
- 합성 데이터 경고 문구(정확히 이 문장): `정상·hard negative 문자는 합성이다. 이 수치를 실제 오탐률로 인용하지 않는다.`
- 잠정 목표: 운영 지점의 dev hard negative 부착률 5% 이하(X), test 스미싱 hit@3 50% 이상. 목표치를 바꾸면 원래 값·바꾼 값·근거를 리포트에 남기고 원래 기준 결과를 지우지 않는다. "결과가 그 값이라서"는 근거가 아니다.
- 설정값(n-gram 범위, TF 방식, 기준값)은 dev 에서만 조정한다. test 지표는 Task 3·5·7 의 리포트 실행에서만 본다. Task 7 은 설정을 정한 뒤 한 번만 돌린다.
- KB 라벨링용 키워드 규칙은 저장소와 검색 코드에 넣지 않는다. 세션 스크래치패드에서만 쓴다.
- 평가 코드는 런타임 패키지 `ai`(`ai/src/ai/`)에 넣지 않는다.
- 작업 디렉터리는 `ai/` 다. 이 저장소 경로의 한글 때문에 `uv run`·`.venv/Scripts/python` 은 `.pth` 를 cp949 로 읽다 실패한다. 테스트·평가는 Bash 에서 아래처럼 돌린다. 현재 기준 `807 passed`.
  ```bash
  PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q
  ```
- 스크래치패드(`$SCRATCH`): 셸 상태는 명령 사이에 남지 않으므로 `$SCRATCH` 를 쓰는 Bash 명령마다 앞에 아래 정의를 붙인다.
  ```bash
  SCRATCH="C:/Users/ksy/AppData/Local/Temp/claude/C--Users-ksy-OneDrive-------ktc4-chonnam-1/b9aca8f0-e063-4ce8-9053-ba7de9f03060/scratchpad"
  ```
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 를 붙인다. push·PR 생성은 사용자 확인 후에만 한다.
- Task 1~3 은 `refactor/scenario-message-test-2-ai` 에서 한다. Task 4 는 그 위에 만든 `feature/kb-category-labels` 에서 한다. Task 5~7 은 라벨링 PR 머지 뒤 머지된 브랜치에서 한다.

## Review Focus

1. 정규화하면 빈 문자열이 되는 질의(`"!!! ???"`, 기호만): 0으로 나누지 않고 `COMPLETED` 와 유사도 0.0을 돌려줘야 한다. → Task 6 `test_query_without_searchable_characters_scores_zero`
2. 기준값이 보고된 유사도와 정확히 같을 때: 평가 도구와 운영 검색이 같은 사례를 남겨야 한다(반올림 전 원점수로 비교하면 어긋난다). → Task 6 `test_threshold_equal_to_reported_similarity_keeps_the_match`
3. KB frontmatter 의 유형 코드 오타: `_parse_case` 가 모르는 코드를 조용히 버려 hit 실패로만 보인다. 저장된 frontmatter 를 직접 검사해 테스트가 실패해야 한다. → Task 4 `test_kb_categories_are_known_and_single`
4. 같은 템플릿에서 나온 정상 문자: dev 와 test 에 갈라지면 누수다. 한쪽에만 들어가야 한다. → Task 1 `test_rows_from_one_template_land_in_the_same_half`
5. test 행이 0건이거나 KB 에만 있는 유형: 유형별 표가 `0/0` 으로 나오고 멈추지 않아야 한다. → Task 2 `test_category_table_counts_rows_and_keeps_kb_only_categories`

## 파일 지도

| 파일 | 책임 | Task |
|---|---|---|
| `ai/eval/rag_metrics.py` (생성) | 분할, hit@k, MRR, AUC, 부착률, 운영 지점 선택. 순수 함수 | 1 |
| `ai/tests/test_rag_metrics.py` (생성) | 지표 함수 단위 테스트 | 1 |
| `ai/eval/rag_eval.py` (생성) | 테스트셋 읽기 → 검색 → 지표 → 리포트, CLI | 2, 7 |
| `ai/tests/test_rag_eval.py` (생성) | 평가 흐름·리포트 테스트 | 2, 7 |
| `ai/eval/README.md` | RAG 평가 실행 방법 | 3 |
| `ai/eval/reports/<실행일>-rag-<방식>.md` (생성) | 기준선·개선 리포트 | 3, 5, 7 |
| `ai/tests/test_kb_data.py` | KB 유형 형식 검사 추가 | 4 |
| `ai/src/ai/kb/case_examples/CE-*.md` | `categories` 한 줄만 변경 | 4 |
| `ai/src/ai/kb/search.py` | TF-IDF 코사인 색인·순위, 기준값 비교 | 6, 7 |
| `ai/tests/test_search.py` | TF-IDF 동작 테스트 추가 | 6 |
| `docs/latency-budget.md` | 사례 검색 실측값 갱신 | 7 |

---

### Task 1: 평가 지표 함수

**Files:**
- Create: `ai/eval/rag_metrics.py`
- Test: `ai/tests/test_rag_metrics.py`

**Interfaces:**
- Consumes: `ai.types.CaseMatch` (`case_id: str`, `similarity: float`, `matched_variant: str`, `categories: list[CategoryCode]`). `CategoryCode` 는 `str` Enum 이라 `"delivery" in [CategoryCode.DELIVERY]` 가 참이다.
- Produces (모두 `rag_metrics` 모듈):
  - `split_key(row: dict) -> str`
  - `is_dev(row: dict) -> bool`
  - `top1(matches: Sequence[CaseMatch]) -> float`
  - `attached(matches: Sequence[CaseMatch], threshold: float) -> bool`
  - `first_hit_rank(matches: Sequence[CaseMatch], category: str, threshold: float, k: int = 3) -> int | None`
  - `hit_rate(ranks: Sequence[int | None], k: int) -> float`
  - `mrr(ranks: Sequence[int | None]) -> float`
  - `attach_rate(match_lists: Sequence[Sequence[CaseMatch]], threshold: float) -> float`
  - `auc(positives: Sequence[float], negatives: Sequence[float]) -> float`
  - `pick_threshold(constrained: Sequence[Sequence[CaseMatch]], max_rate: float, candidates: Iterable[float]) -> float`
  - `matches` 는 항상 점수 내림차순이라고 가정한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_rag_metrics.py`:

```python
import sys
from pathlib import Path

import pytest

# 평가 코드는 런타임 패키지 밖(ai/eval)에 있어 경로로 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import rag_metrics as metrics  # noqa: E402
from ai.types import CaseMatch, CategoryCode  # noqa: E402


def _match(score, category="delivery"):
    return CaseMatch(
        case_id=f"CE-{score}",
        similarity=score,
        matched_variant="사례",
        categories=[CategoryCode(category)],
    )


def test_rows_from_one_template_land_in_the_same_half():
    rows = [{"id": f"B-delivery-{i:02d}", "template": "B-delivery-t1"} for i in range(1, 6)]

    assert len({metrics.is_dev(row) for row in rows}) == 1


def test_rows_without_template_split_by_id():
    assert metrics.split_key({"id": "S-penalty-01", "template": None}) == "S-penalty-01"
    assert metrics.split_key({"id": "B-penalty-01", "template": "B-penalty-t2"}) == "B-penalty-t2"


def test_split_is_roughly_half_and_stable():
    rows = [{"id": f"S-x-{i:03d}", "template": None} for i in range(200)]
    first = [metrics.is_dev(row) for row in rows]

    assert first == [metrics.is_dev(row) for row in rows]
    assert 70 <= sum(first) <= 130


def test_first_hit_rank_skips_wrong_type_and_stops_below_threshold():
    matches = [_match(0.5, "payment"), _match(0.4, "delivery"), _match(0.2, "delivery")]

    assert metrics.first_hit_rank(matches, "delivery", 0.3) == 2
    assert metrics.first_hit_rank(matches, "delivery", 0.45) is None
    assert metrics.first_hit_rank(matches, "delivery", 0.3, k=1) is None
    assert metrics.first_hit_rank([], "delivery", 0.0) is None


def test_hit_rate_and_mrr():
    ranks = [1, 3, None, 2]

    assert metrics.hit_rate(ranks, 1) == 0.25
    assert metrics.hit_rate(ranks, 3) == 0.75
    assert metrics.mrr(ranks) == pytest.approx((1 + 1 / 3 + 1 / 2) / 4)


def test_attach_is_inclusive_and_needs_a_match():
    assert metrics.attached([_match(0.3)], 0.3)
    assert not metrics.attached([_match(0.2999)], 0.3)
    assert not metrics.attached([], 0.0)
    assert metrics.attach_rate([[_match(0.3)], [_match(0.1)], []], 0.3) == pytest.approx(1 / 3)


def test_auc_counts_ties_as_half():
    assert metrics.auc([0.9, 0.5], [0.5, 0.1]) == 0.875


def test_pick_threshold_takes_lowest_candidate_that_meets_the_rate():
    hard_negatives = [[_match(score)] for score in (0.1, 0.2, 0.3, 0.4)]

    assert metrics.pick_threshold(hard_negatives, 0.25, [0.05, 0.15, 0.35, 0.4]) == 0.35


def test_pick_threshold_goes_above_tied_maximum_when_no_candidate_fits():
    hard_negatives = [[_match(0.5)], [_match(0.5)]]

    assert metrics.pick_threshold(hard_negatives, 0.0, [0.1, 0.5]) == 0.5001
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag_metrics'`

- [ ] **Step 3: 구현**

`ai/eval/rag_metrics.py`:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_metrics.py -q`
Expected: `9 passed`

- [ ] **Step 5: 커밋**

```bash
git add eval/rag_metrics.py tests/test_rag_metrics.py
git commit -m "feat: RAG 평가 지표 함수 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 평가 실행·리포트 도구

**Files:**
- Create: `ai/eval/rag_eval.py`
- Test: `ai/tests/test_rag_eval.py`

**Interfaces:**
- Consumes: Task 1 의 `rag_metrics` 전체. `ai.kb.search.load_cases() -> tuple[Case, ...]`(`Case.categories: tuple[CategoryCode, ...]`), `ai.kb.search.search_cases(text, *, min_similarity, top_k) -> CaseSearchResult`.
- Produces (`rag_eval` 모듈):
  - `Searcher = Callable[[str], list[CaseMatch]]` — 점수 내림차순 상위 3개, 기준값 미적용
  - `@dataclass(frozen=True) RowResult(row: dict, matches: list[CaseMatch], seconds: float)`
  - `default_searcher(text: str) -> list[CaseMatch]`
  - `load_rows(path: Path = TESTSET) -> list[dict]`
  - `run_search(rows: Sequence[dict], searcher: Searcher) -> list[RowResult]`
  - `summarize(results: Sequence[RowResult], threshold: float) -> dict[str, dict]` — 그룹별 `n`, `keys`, `attach`, 스미싱은 `hit1`·`hit3`·`mrr`, 정상 그룹은 `auc`
  - `category_table(results, threshold, kb_counts: Mapping[str, int]) -> list[dict]` — 행마다 `category`, `kb`, `smishing`·`benign`·`hard_negative` = `(맞은 수, 전체 수)`
  - `failures(results, threshold) -> tuple[list[RowResult], list[RowResult]]`
  - `evaluate(results, kb_counts, *, include_test=True, max_rate=MAX_HARD_NEGATIVE_RATE, is_dev=metrics.is_dev) -> dict` — 키 `threshold`, `kb_counts`, `dev`, (include_test 면) `test`, `categories`, `misses`, `attached`
  - `latency(results) -> dict[str, float]` — `first_ms`, `p50_ms`, `p95_ms`
  - `render_report(evaluation: dict, meta: dict) -> str` — meta 키 `label`, `date`, `commit`, `kb_total`, `settings`, `latency`
  - `main(argv: Sequence[str] | None = None) -> int` — `--label`(필수), `--dev-only`
  - 상수 `TOP_K = 3`, `FAILURE_LIMIT = 10`, `MAX_HARD_NEGATIVE_RATE = 0.05`, `TARGET_HIT_AT_3 = 0.50`, `SYNTHETIC_WARNING`

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_rag_eval.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import rag_eval  # noqa: E402
from ai.types import CaseMatch, CategoryCode  # noqa: E402


def _match(score, category="delivery"):
    return CaseMatch(
        case_id=f"CE-{score}",
        similarity=score,
        matched_variant=f"사례 {score}",
        categories=[CategoryCode(category)],
    )


def _result(row_id, group, category, score, matched_category="delivery", text=None):
    row = {
        "id": row_id,
        "group": group,
        "category": category,
        "text": text or f"{row_id} 본문",
        "template": None,
    }
    return rag_eval.RowResult(row, [_match(score, matched_category)], 0.0)


# dev: hard negative 0.2·0.4 → max_rate 0.5 에서 기준값 0.4
RESULTS = [
    _result("dev-s1", "smishing", "delivery", 0.6),
    _result("dev-h1", "hard_negative", "delivery", 0.2),
    _result("dev-h2", "hard_negative", "delivery", 0.4),
    _result("dev-b1", "benign", "delivery", 0.1),
    _result("test-s1", "smishing", "delivery", 0.5),
    _result("test-s2", "smishing", "penalty", 0.45),
    _result("test-h1", "hard_negative", "penalty", 0.42),
    _result("test-b1", "benign", "delivery", 0.05),
]


def _is_dev(row):
    return row["id"].startswith("dev-")


def _evaluate(results=RESULTS, kb_counts=None, **kwargs):
    kb_counts = {"delivery": 3} if kb_counts is None else kb_counts
    return rag_eval.evaluate(results, kb_counts, max_rate=0.5, is_dev=_is_dev, **kwargs)


def test_threshold_is_picked_on_dev_and_applied_to_test():
    evaluation = _evaluate()

    assert evaluation["threshold"] == 0.4
    test = evaluation["test"]
    assert test["smishing"]["n"] == 2
    assert test["smishing"]["hit3"] == 0.5
    assert test["hard_negative"]["attach"] == 1.0
    assert test["benign"]["attach"] == 0.0
    assert test["hard_negative"]["auc"] == 1.0


def test_failures_list_test_rows_only():
    evaluation = _evaluate()

    assert [result.row["id"] for result in evaluation["misses"]] == ["test-s2"]
    assert [result.row["id"] for result in evaluation["attached"]] == ["test-h1"]


def test_dev_only_leaves_test_unseen():
    evaluation = _evaluate(include_test=False)

    assert "test" not in evaluation
    assert "categories" not in evaluation
    assert evaluation["dev"]["smishing"]["n"] == 1


def test_category_table_counts_rows_and_keeps_kb_only_categories():
    evaluation = _evaluate(kb_counts={"delivery": 3, "obituary": 2})
    table = {row["category"]: row for row in evaluation["categories"]}

    assert table["delivery"]["smishing"] == (1, 1)
    assert table["delivery"]["benign"] == (0, 1)
    assert table["penalty"]["hard_negative"] == (1, 1)
    assert table["obituary"] == {
        "category": "obituary",
        "kb": 2,
        "smishing": (0, 0),
        "benign": (0, 0),
        "hard_negative": (0, 0),
    }


def test_report_flags_unlabelled_kb_and_escapes_cells():
    odd = _result("test-b2", "benign", "delivery", 0.9, text="줄\n바꿈 | 파이프")
    evaluation = _evaluate(results=[*RESULTS, odd], kb_counts={})
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(evaluation, meta)

    assert rag_eval.SYNTHETIC_WARNING in report
    assert "측정 불가" in report
    assert "기준값: 0.4000" in report
    assert "줄 바꿈 \\| 파이프" in report
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_eval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag_eval'`

- [ ] **Step 3: 구현**

`ai/eval/rag_eval.py`:

```python
"""RAG 사례 검색 평가.

ai/ 에서 실행한다.

    python eval/rag_eval.py --label <방식>             # test 까지 재고 리포트를 쓴다
    python eval/rag_eval.py --label <방식> --dev-only  # 설정 조정용. dev 지표만 출력한다

지표 정의와 규칙은 docs/superpowers/specs/2026-10-02-rag-eval-improvement-design.md 를 따른다.
"""

import argparse
import json
import statistics
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import rag_metrics as metrics
from ai.kb.search import load_cases, search_cases
from ai.types import CaseMatch

EVAL_DIR = Path(__file__).resolve().parent
TESTSET = EVAL_DIR / "datasets" / "rag_testset.jsonl"
REPORTS = EVAL_DIR / "reports"
GROUPS = ("smishing", "benign", "hard_negative")
NEGATIVE_GROUPS = ("benign", "hard_negative")
TOP_K = 3
FAILURE_LIMIT = 10
# 잠정 목표치. 바꿀 때는 원래 값·바꾼 값·근거를 리포트에 남긴다.
MAX_HARD_NEGATIVE_RATE = 0.05
TARGET_HIT_AT_3 = 0.50
SYNTHETIC_WARNING = "정상·hard negative 문자는 합성이다. 이 수치를 실제 오탐률로 인용하지 않는다."

Searcher = Callable[[str], list[CaseMatch]]


@dataclass(frozen=True)
class RowResult:
    row: dict
    matches: list[CaseMatch]
    seconds: float


def default_searcher(text: str) -> list[CaseMatch]:
    return search_cases(text, min_similarity=0.0, top_k=TOP_K).matches


def load_rows(path: Path = TESTSET) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_search(rows: Sequence[dict], searcher: Searcher) -> list[RowResult]:
    results = []
    for row in rows:
        start = time.perf_counter()
        matches = searcher(row["text"])
        results.append(RowResult(row, matches, time.perf_counter() - start))
    return results


def _in_group(results: Sequence[RowResult], group: str) -> list[RowResult]:
    return [result for result in results if result.row["group"] == group]


def _rank(result: RowResult, threshold: float) -> int | None:
    return metrics.first_hit_rank(result.matches, result.row["category"], threshold, TOP_K)


def summarize(results: Sequence[RowResult], threshold: float) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for group in GROUPS:
        part = _in_group(results, group)
        if not part:
            continue
        entry = {
            "n": len(part),
            "keys": len({metrics.split_key(result.row) for result in part}),
            "attach": metrics.attach_rate([result.matches for result in part], threshold),
        }
        if group == "smishing":
            ranks = [_rank(result, threshold) for result in part]
            entry |= {
                "hit1": metrics.hit_rate(ranks, 1),
                "hit3": metrics.hit_rate(ranks, 3),
                "mrr": metrics.mrr(ranks),
            }
        summary[group] = entry

    positives = [metrics.top1(result.matches) for result in _in_group(results, "smishing")]
    for group in NEGATIVE_GROUPS:
        negatives = [metrics.top1(result.matches) for result in _in_group(results, group)]
        if positives and negatives:
            summary[group]["auc"] = metrics.auc(positives, negatives)
    return summary


def category_table(
    results: Sequence[RowResult], threshold: float, kb_counts: Mapping[str, int]
) -> list[dict]:
    # 유형별 test 표본은 몇 건뿐이라 백분율이 아니라 건수로 적는다.
    table = []
    for category in sorted({result.row["category"] for result in results} | set(kb_counts)):
        same = [result for result in results if result.row["category"] == category]
        smishing = _in_group(same, "smishing")
        entry = {
            "category": category,
            "kb": kb_counts.get(category, 0),
            "smishing": (sum(_rank(result, threshold) is not None for result in smishing), len(smishing)),
        }
        for group in NEGATIVE_GROUPS:
            part = _in_group(same, group)
            entry[group] = (sum(metrics.attached(result.matches, threshold) for result in part), len(part))
        table.append(entry)
    return table


def failures(
    results: Sequence[RowResult], threshold: float
) -> tuple[list[RowResult], list[RowResult]]:
    # 점수가 높은데 틀린 행이 원인을 가장 잘 보여준다. top-1 내림차순으로 자른다.
    def by_score(result: RowResult) -> float:
        return -metrics.top1(result.matches)

    misses = [result for result in _in_group(results, "smishing") if _rank(result, threshold) is None]
    wrong = [
        result
        for result in results
        if result.row["group"] in NEGATIVE_GROUPS and metrics.attached(result.matches, threshold)
    ]
    return sorted(misses, key=by_score)[:FAILURE_LIMIT], sorted(wrong, key=by_score)[:FAILURE_LIMIT]


def evaluate(
    results: Sequence[RowResult],
    kb_counts: Mapping[str, int],
    *,
    include_test: bool = True,
    max_rate: float = MAX_HARD_NEGATIVE_RATE,
    is_dev: Callable[[dict], bool] = metrics.is_dev,
) -> dict:
    dev = [result for result in results if is_dev(result.row)]
    test = [result for result in results if not is_dev(result.row)]
    threshold = metrics.pick_threshold(
        [result.matches for result in _in_group(dev, "hard_negative")],
        max_rate,
        [metrics.top1(result.matches) for result in dev],
    )
    evaluation = {"threshold": threshold, "kb_counts": dict(kb_counts), "dev": summarize(dev, threshold)}
    if include_test:
        evaluation["test"] = summarize(test, threshold)
        evaluation["categories"] = category_table(test, threshold, kb_counts)
        evaluation["misses"], evaluation["attached"] = failures(test, threshold)
    return evaluation


def latency(results: Sequence[RowResult]) -> dict[str, float]:
    # 첫 호출에는 KB 로드가 들어간다. 나머지로 질의 1건 소요를 잰다.
    rest = [result.seconds * 1000 for result in results[1:]]
    return {
        "first_ms": results[0].seconds * 1000,
        "p50_ms": statistics.median(rest),
        "p95_ms": statistics.quantiles(rest, n=20)[18],
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cell(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _count(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]}"


def _summary_lines(title: str, summary: dict[str, dict]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| 그룹 | 행 | 분할 키 | 부착률 | hit@1 | hit@3 | MRR | AUC (스미싱 대비) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for group in GROUPS:
        entry = summary.get(group)
        if entry is None:
            continue
        cells = [
            group,
            str(entry["n"]),
            str(entry["keys"]),
            _pct(entry["attach"]),
            _pct(entry["hit1"]) if "hit1" in entry else "—",
            _pct(entry["hit3"]) if "hit3" in entry else "—",
            f"{entry['mrr']:.3f}" if "mrr" in entry else "—",
            f"{entry['auc']:.3f}" if "auc" in entry else "—",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, ""]


def _failure_lines(title: str, results: Sequence[RowResult]) -> list[str]:
    lines = [f"### {title}", ""]
    if not results:
        return [*lines, "없음.", ""]
    lines += ["| id | top-1 | 원문 | 붙은 사례 |", "|---|---|---|---|"]
    for result in results:
        if result.matches:
            top = result.matches[0]
            codes = ", ".join(code.value for code in top.categories)
            case = f"{top.case_id} [{codes}] {_cell(top.matched_variant)}"
        else:
            case = "—"
        lines.append(
            f"| {result.row['id']} | {metrics.top1(result.matches):.4f} | {_cell(result.row['text'])} | {case} |"
        )
    return [*lines, ""]


def render_report(evaluation: dict, meta: dict) -> str:
    kb_counts = evaluation["kb_counts"]
    labelled = sum(kb_counts.values())
    hit3 = evaluation["test"]["smishing"]["hit3"]
    verdict = "충족" if hit3 >= TARGET_HIT_AT_3 else "미충족"
    lines = [
        f"# RAG 사례 검색 평가: {meta['label']}",
        "",
        f"실행일: {meta['date']} · 커밋: `{meta['commit']}` · KB: {meta['kb_total']}건 (유형 라벨 {labelled}건)",
        "",
        f"설정: {meta['settings']}",
        "",
        f"> {SYNTHETIC_WARNING}",
        "",
    ]
    if not labelled:
        lines += [
            "> KB 사례에 유형 라벨이 없어 hit@k·MRR 은 측정 불가다. 0 은 검색 실패를 뜻하지 않는다.",
            "",
        ]
    lines += [
        "## 운영 지점",
        "",
        f"- 기준값: {evaluation['threshold']:.4f} (dev hard negative 부착률 {_pct(MAX_HARD_NEGATIVE_RATE)} 이하가 되는 가장 낮은 값)",
        f"- dev hard negative 부착률: {_pct(evaluation['dev']['hard_negative']['attach'])}",
        f"- test 스미싱 hit@3: {_pct(hit3)} — 잠정 목표 {_pct(TARGET_HIT_AT_3)} {verdict}",
        "",
        "## 그룹별 지표",
        "",
        *_summary_lines("dev", evaluation["dev"]),
        *_summary_lines("test", evaluation["test"]),
        "## 유형별 (test, 건수)",
        "",
        "| 유형 | KB | 스미싱 hit@3 | 정상 부착 | hard negative 부착 |",
        "|---|---|---|---|---|",
        *(
            f"| {row['category']} | {row['kb']} | {_count(row['smishing'])} | {_count(row['benign'])} | {_count(row['hard_negative'])} |"
            for row in evaluation["categories"]
        ),
        "",
        "## 지연",
        "",
        f"- 첫 호출(KB 로드 포함): {meta['latency']['first_ms']:.1f}ms",
        f"- 질의 1건: p50 {meta['latency']['p50_ms']:.2f}ms · p95 {meta['latency']['p95_ms']:.2f}ms (예산 1,000ms)",
        "",
        "## 실패 사례 (test)",
        "",
        *_failure_lines(f"스미싱 hit@3 실패 상위 {FAILURE_LIMIT}건", evaluation["misses"]),
        *_failure_lines(f"사례가 붙은 정상 문자 상위 {FAILURE_LIMIT}건", evaluation["attached"]),
    ]
    return "\n".join(lines)


def _commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=EVAL_DIR
        ).stdout.strip()

    return git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain") else "")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG 사례 검색 평가")
    parser.add_argument("--label", required=True, help="리포트 파일 이름에 붙일 방식 이름")
    parser.add_argument(
        "--dev-only", action="store_true", help="dev 지표만 출력하고 리포트를 쓰지 않는다"
    )
    args = parser.parse_args(argv)

    results = run_search(load_rows(), default_searcher)
    cases = load_cases()
    kb_counts = Counter(code.value for case in cases for code in case.categories)
    evaluation = evaluate(results, kb_counts, include_test=not args.dev_only)
    if args.dev_only:
        print(
            json.dumps(
                {"threshold": evaluation["threshold"], "dev": evaluation["dev"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    meta = {
        "label": args.label,
        "date": date.today().isoformat(),
        "commit": _commit(),
        "kb_total": len(cases),
        "settings": "문자 3-gram Jaccard (search_cases 기본값)",
        "latency": latency(results),
    }
    path = REPORTS / f"{meta['date']}-rag-{args.label}.md"
    path.write_text(render_report(evaluation, meta), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_eval.py tests/test_rag_metrics.py -q`
Expected: `14 passed`

- [ ] **Step 5: 커밋**

```bash
git add eval/rag_eval.py tests/test_rag_eval.py
git commit -m "feat: RAG 평가 실행·리포트 도구 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 기준선 리포트와 실행 방법

**Files:**
- Modify: `ai/eval/README.md` (끝에 섹션 추가)
- Create: `ai/eval/reports/<실행일>-rag-jaccard.md` (스크립트가 생성)

**Interfaces:**
- Consumes: `python eval/rag_eval.py --label jaccard` (Task 2)
- Produces: 기준선 리포트. Task 5·7 리포트의 비교 대상이다.

- [ ] **Step 1: README 에 실행 방법 추가**

`ai/eval/README.md` 끝에 붙인다:

````markdown

## RAG 사례 검색 평가

`ai/` 에서 실행한다. 의존성 설치는 `ai/tests/README.md` 를 따른다.

```bash
python eval/rag_eval.py --label <방식>             # test 까지 재고 reports/ 에 리포트를 쓴다
python eval/rag_eval.py --label <방식> --dev-only  # 설정 조정용. dev 지표만 출력한다
```

Windows 에서 저장소 경로에 한글이 있으면 `.venv` 의 Python 이 `.pth` 를 cp949 로 읽다가
실패한다. 그때는 시스템 Python(3.11 이상)에 경로를 직접 넘긴다.

```bash
PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label <방식>
```

지표 정의, dev/test 분할, 운영 지점, 잠정 목표와 변경 규칙은
[설계 문서](../../docs/superpowers/specs/2026-10-02-rag-eval-improvement-design.md)를 따른다.
설정값은 dev 에서만 조정하고 test 는 설정을 정한 뒤 한 번만 본다.
````

- [ ] **Step 2: 기준선 실행**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label jaccard`
Expected: `.../ai/eval/reports/<실행일>-rag-jaccard.md` 경로가 출력된다.

- [ ] **Step 3: 리포트 확인**

리포트를 열어 아래를 확인한다. 어긋나면 커밋하지 말고 원인을 찾는다.
- "KB 사례에 유형 라벨이 없어 hit@k·MRR 은 측정 불가다" 문장이 있다(유형 라벨 0건).
- 합성 데이터 경고 문장이 있다.
- dev/test 각 그룹 행 수의 합이 smishing 215, benign 280, hard_negative 280 이다.
- 질의 p95 가 1,000ms 보다 훨씬 작다.
- AUC 가 2026-10-02 임시 측정(스미싱 대비 정상 0.464, hard negative 0.560)과 크게 다르지 않다. test 절반이라 조금 다를 수 있다.

- [ ] **Step 4: 전체 테스트**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `821 passed` (기존 807 + 14)

- [ ] **Step 5: 커밋**

```bash
git add eval/README.md eval/reports/*-rag-jaccard.md
git commit -m "docs: RAG 검색 기준선 리포트와 실행 방법 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: KB 유형 라벨링 (별도 PR)

**Files:**
- Modify: `ai/tests/test_kb_data.py` (테스트 추가)
- Modify: `ai/src/ai/kb/case_examples/CE-*.md` (`categories:` 한 줄만)
- Scratch (커밋하지 않음): `$SCRATCH/kb_label_candidates.py`, `$SCRATCH/kb_candidates.csv`, `$SCRATCH/kb_labels.json`, `$SCRATCH/kb_label_notes.json`, `$SCRATCH/kb_apply_labels.py`, `$SCRATCH/kb_pr_body.py`, `$SCRATCH/kb_pr_body.md`

**Interfaces:**
- Consumes: `ai.kb.search.load_cases()`, `ai.kb.normalize.normalize()`
- Produces: 사례마다 `categories: [<code>]` 또는 `categories: []`(확신 없음). 코드는 `CategoryCode` 14개 중 하나이며 `other`·`unknown` 은 쓰지 않는다.

유형 해석(라벨 판단 기준, PR 본문에도 싣는다):

| 코드 | 뜻 |
|---|---|
| `delivery` | 택배 배송·보관·반송·통관 안내 사칭 |
| `address_correction` | 주소 불일치·주소 수정 요구 |
| `payment` | 결제·구매 승인 알림 사칭 |
| `penalty` | 과태료·범칙금·벌점 고지 |
| `card_or_account` | 카드·계좌·대출 |
| `public_refund` | 국세·지방세·보험료 등 공공 환급 |
| `public_support` | 정부 지원금·재난지원금 |
| `acquaintance_impersonation` | 가족·지인 사칭 |
| `invitation` | 청첩장·초대장 |
| `obituary` | 부고 |
| `prize_or_event` | 당첨·경품·이벤트 |
| `health_check` | 건강검진 |
| `telecom_refund` | 통신 요금 환급·과납·이용대금 명세서 |
| `account_security` | 계정 로그인·보안 경고 |

- [ ] **Step 1: 브랜치 생성**

```bash
git switch -c feature/kb-category-labels
```

- [ ] **Step 2: 유형 형식 검사 테스트 작성**

`ai/tests/test_kb_data.py` 의 `test_kb_stored_normalized_field_is_not_stale` 바로 아래에 추가한다:

```python
def test_kb_categories_are_known_and_single():
    # _parse_case 는 모르는 유형 코드를 조용히 버린다. 오타가 나면 검색 평가에서
    # hit 실패로만 보이므로 저장된 frontmatter 를 직접 검사한다. 테스트셋처럼
    # 대표 유형 하나만 붙인다. 여러 개를 허용하면 hit 이 부풀려진다.
    import yaml

    allowed = {code.value for code in CategoryCode} - {"other", "unknown"}
    for path in sorted(CASES_DIR.glob("CE-*.md")):
        raw = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", raw, re.DOTALL)
        if match is None:
            continue
        categories = yaml.safe_load(match.group(1)).get("categories") or []
        assert len(categories) <= 1, path.name
        assert set(categories) <= allowed, path.name
```

- [ ] **Step 3: 테스트가 오타를 잡는지 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_kb_data.py::test_kb_categories_are_known_and_single -q`
Expected: `1 passed` (현재 전부 `[]`)

`CE-0100.md` 의 `categories: []` 를 `categories: [delivry]` 로 바꾸고 다시 돌린다.
Expected: FAIL — `AssertionError: CE-0100.md`

```bash
git checkout -- src/ai/kb/case_examples/CE-0100.md
```

- [ ] **Step 4: 커밋**

```bash
git add tests/test_kb_data.py
git commit -m "test: KB 유형 라벨 형식 검사 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: 키워드 후보 생성 (스크래치, 커밋 금지)**

`$SCRATCH/kb_label_candidates.py`:

```python
"""일회성 KB 유형 후보 생성기. 저장소에 커밋하지 않고 검색 코드에서 쓰지 않는다."""

import csv
import sys

sys.path.insert(0, "src")

from ai.kb.normalize import normalize  # noqa: E402
from ai.kb.search import load_cases  # noqa: E402

# 순서가 동점 우선순위다. 구체적인 유형을 앞에 둔다.
KEYWORDS = {
    "obituary": ["부고", "별세", "장례", "빈소", "발인", "부친", "모친", "작고"],
    "invitation": ["청첩", "결혼", "초대", "돌잔치", "웨딩"],
    "health_check": ["건강검진", "검진", "검사결과"],
    "telecom_refund": ["통신", "요금", "과납", "이용대금", "명세서", "skt", "lgu"],
    "public_refund": ["환급", "국세", "세금", "연말정산", "지방세"],
    "public_support": ["지원금", "재난", "민생", "보조금", "소상공인", "장려금"],
    "penalty": ["과태료", "범칙금", "벌점", "위반", "주정차", "체납", "고지서", "벌금"],
    "address_correction": ["주소", "도로명", "우편번호"],
    "acquaintance_impersonation": ["엄마", "아빠", "아들", "액정", "폰고장", "문화상품권", "송금"],
    "prize_or_event": ["당첨", "경품", "이벤트", "쿠폰", "사은품", "추첨"],
    "account_security": ["로그인", "비밀번호", "계정", "보안", "해외접속", "개인정보"],
    "card_or_account": ["카드", "계좌", "은행", "대출", "한도", "발급"],
    "payment": ["결제", "승인", "구매", "주문", "출금", "결재"],
    "delivery": ["택배", "배송", "운송장", "반송", "보관", "배달", "소포", "등기", "물품", "통관"],
}


def score(text: str) -> list[tuple[str, list[str]]]:
    found = []
    for category, words in KEYWORDS.items():
        hits = [word for word in words if word in text]
        if hits:
            found.append((category, hits))
    # sorted 는 안정 정렬이라 동점이면 KEYWORDS 순서가 남는다.
    return sorted(found, key=lambda item: -len(item[1]))


def main(path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["id", "candidate", "runner_up", "keywords", "text"])
        for case in load_cases():
            ranked = score(" ".join(case.normalized))
            writer.writerow([
                case.case_id,
                ranked[0][0] if ranked else "",
                ranked[1][0] if len(ranked) > 1 else "",
                ",".join(ranked[0][1]) if ranked else "",
                " ⏐ ".join(case.variants),
            ])


# kb_pr_body.py 가 KEYWORDS 를 import 하므로 실행부를 감싼다.
if __name__ == "__main__":
    main(sys.argv[1])
```

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python "$SCRATCH/kb_label_candidates.py" "$SCRATCH/kb_candidates.csv"`
Expected: `kb_candidates.csv` 에 헤더 + 296행.

- [ ] **Step 6: 후보 검토 후 라벨 확정**

`kb_candidates.csv` 를 50행씩 읽으며 행마다 유형을 확정한다. 규칙:
- 문자가 내세우는 용건 하나로 고른다. 키워드 후보는 참고일 뿐이다.
- 택배 + 주소: 주소 확인·수정 요구가 핵심이면 `address_correction`, 아니면 `delivery`.
- 위 유형 해석 표의 어느 것에도 확신이 없으면 `null`.

결과를 두 파일로 쓴다:
- `$SCRATCH/kb_labels.json`: `{"CE-0002": "delivery", "CE-0100": "delivery", ..., "CE-0xxx": null}` — 296개 id 전부
- `$SCRATCH/kb_label_notes.json`: `null` 로 둔 id 와 후보를 뒤집은 id 에 대해 `{"CE-0xxx": "이유 한 문장"}`

확인:

```bash
PYTHONUTF8=1 python -c "
import json, collections
labels = json.load(open(r'$SCRATCH/kb_labels.json', encoding='utf-8'))
print(len(labels), collections.Counter(labels.values()))"
```
Expected: 첫 값 `296`. 유형 값은 14개 코드와 `None` 뿐이다.

- [ ] **Step 7: 유형별로 반영하고 커밋**

`$SCRATCH/kb_apply_labels.py`:

```python
"""kb_labels.json 에서 한 유형으로 확정된 사례의 categories 줄만 바꾼다."""

import json
import re
import sys
from pathlib import Path

labels = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
category = sys.argv[2]
cases_dir = Path("src/ai/kb/case_examples")
changed = 0
for case_id, label in labels.items():
    if label != category:
        continue
    path = cases_dir / f"{case_id}.md"
    # newline="" 로 줄바꿈을 그대로 둔다. 바꾸면 파일 전체가 diff 에 잡힌다.
    with open(path, encoding="utf-8", newline="") as file:
        raw = file.read()
    new, count = re.subn(r"^categories: \[\]", f"categories: [{category}]", raw, count=1, flags=re.MULTILINE)
    assert count == 1, case_id
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(new)
    changed += 1
print(category, changed)
```

유형마다 반영·커밋한다. 0건인 유형은 커밋하지 않는다:

```bash
for category in delivery address_correction payment penalty card_or_account public_refund public_support acquaintance_impersonation invitation obituary prize_or_event health_check telecom_refund account_security; do
  out=$(PYTHONUTF8=1 python "$SCRATCH/kb_apply_labels.py" "$SCRATCH/kb_labels.json" "$category")
  count=${out##* }
  if [ "$count" != "0" ]; then
    git add src/ai/kb/case_examples
    git commit -q -m "feat(kb): $category 유형 라벨 ${count}건" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  fi
  echo "$out"
done
```

- [ ] **Step 8: categories 줄 외에는 바뀌지 않았는지 확인**

```bash
git diff -U0 refactor/scenario-message-test-2-ai -- src/ai/kb/case_examples | grep '^[-+]' | grep -v '^[-+]categories:' | grep -v '^+++\|^---'
```
Expected: 출력 없음.

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `822 passed`

- [ ] **Step 9: PR 본문 생성**

`$SCRATCH/kb_pr_body.py`:

```python
"""라벨 결과로 PR 본문을 만든다."""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))
from kb_label_candidates import KEYWORDS  # noqa: E402

scratch = Path(sys.argv[1])
labels = json.loads((scratch / "kb_labels.json").read_text(encoding="utf-8"))
notes = json.loads((scratch / "kb_label_notes.json").read_text(encoding="utf-8"))
counts = collections.Counter(label for label in labels.values() if label)
empty = sorted(case_id for case_id, label in labels.items() if label is None)

lines = [
    "## 변경 요약",
    "",
    f"KB curated 사례 {len(labels)}건 중 {sum(counts.values())}건에 대표 유형(`categories`) 하나를 붙였다. "
    "RAG 평가에서 \"같은 유형 사례를 찾았는가\"(hit@k)를 재려면 KB 유형이 필요하다. "
    "키워드 규칙으로 후보를 만들고 원문을 읽어 확정했다. 키워드 규칙은 저장소와 검색 코드에 넣지 않았다.",
    "",
    "설계: `docs/superpowers/specs/2026-10-02-rag-eval-improvement-design.md`",
    "",
    "## 리뷰 방법",
    "",
    "커밋이 유형별로 나뉘어 있다. 커밋마다 \"전부 이 유형인가\"만 확인해 주세요. 판단 기준:",
    "",
    "| 유형 | 건수 | 후보 키워드 |",
    "|---|---|---|",
    *(f"| `{code}` | {counts[code]} | {', '.join(KEYWORDS[code])} |" for code in KEYWORDS if counts[code]),
    "",
    f"## 비워 둔 사례 ({len(empty)}건)",
    "",
    "| id | 이유 |",
    "|---|---|",
    *(f"| {case_id} | {notes.get(case_id, '')} |" for case_id in empty),
    "",
    "## 머지 전 주의",
    "",
    "이 라벨로 낸 평가 수치는 머지 전까지 근거로 쓰지 않는다.",
    "",
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
]
(scratch / "kb_pr_body.md").write_text("\n".join(lines), encoding="utf-8")
```

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python "$SCRATCH/kb_pr_body.py" "$SCRATCH"`
Expected: `$SCRATCH/kb_pr_body.md` 생성.

`kb_pr_body.md` 의 "리뷰 방법" 표 아래에 위 "유형 해석" 표를 붙여 넣는다.

- [ ] **Step 10: 사용자 확인 후 push·PR**

사용자에게 PR base 브랜치를 묻는다. 권장은 `refactor/scenario-message-test-2-ai` 다(Task 1~3 커밋이 아직 `develop` 에 없다). 확인을 받은 뒤:

```bash
git push -u origin feature/kb-category-labels
gh pr create --base <확인받은 base> --head feature/kb-category-labels --title "feat(kb): KB 사례 유형 라벨 추가" --body-file "$SCRATCH/kb_pr_body.md"
```

**체크포인트:** PR 이 머지될 때까지 Task 5 로 넘어가지 않는다.

---

### Task 5: 라벨 반영 후 기준선 재측정

**Files:**
- Create: `ai/eval/reports/<실행일>-rag-jaccard-labelled.md`

**Interfaces:**
- Consumes: 머지된 KB 라벨, `python eval/rag_eval.py` (Task 2)
- Produces: 라벨이 들어간 KB 로 잰 현재 검색의 기준선. Task 7 의 비교 대상이다.

- [ ] **Step 1: 머지된 브랜치로 이동**

```bash
git switch <라벨링 PR 의 base>
git pull
grep -l "^categories: \[[a-z_]" src/ai/kb/case_examples/CE-*.md | wc -l
```
Expected: 라벨링 PR 본문의 라벨 건수와 같은 수.

- [ ] **Step 2: 재측정**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label jaccard-labelled`
Expected: 리포트 경로 출력. "측정 불가" 문장이 없다.

- [ ] **Step 3: 해석 섹션 추가**

리포트 끝에 손으로 붙인다:

```markdown
## 해석

- hit@3·MRR 이 처음으로 의미를 갖는 기준선이다. 이전 리포트(`<실행일>-rag-jaccard.md`)는 KB 라벨이 없어 hit 을 잴 수 없었다.
- KB 사례가 2건 이하인 유형: <유형별 표의 KB 열에서 2 이하인 유형을 나열>. 이 유형의 hit 실패는 검색이 아니라 KB 부족으로 분류한다.
```

꺾쇠 안은 리포트의 유형별 표를 보고 채운다. 해당 유형이 없으면 "없음" 이라고 쓴다.

- [ ] **Step 4: 커밋**

```bash
git add eval/reports/*-rag-jaccard-labelled.md
git commit -m "docs: 유형 라벨 반영 후 RAG 기준선 리포트 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 검색을 문자 n-gram TF-IDF 코사인으로 교체

**Files:**
- Modify: `ai/src/ai/kb/search.py` (Jaccard 함수 삭제, 색인·순위 추가, `search_cases` 본문 교체)
- Test: `ai/tests/test_search.py`

**Interfaces:**
- Consumes: 기존 `Case`, `_parse_case`, `load_cases`, `_load_cases`, `_LOAD_LOCK`, `normalize`
- Produces (`ai.kb.search`):
  - `NGRAM_SIZES: tuple[int, ...] = (2, 3)`, `SUBLINEAR_TF: bool = False`, `DEFAULT_MIN_SIMILARITY: float = 0.3`
  - `@dataclass(frozen=True) CaseIndex(cases, vectors, idf: dict[str, float], unseen_idf: float, ngram_sizes, sublinear_tf)`
  - `build_index(cases: Iterable[Case], *, ngram_sizes=NGRAM_SIZES, sublinear_tf=SUBLINEAR_TF) -> CaseIndex`
  - `rank(index: CaseIndex, masked_text: str) -> list[CaseMatch]` — 모든 사례, 유사도 내림차순, 동점은 KB 순서
  - `load_index(cases_dir: Path = CASES_DIR) -> CaseIndex` — 프로세스 수명 캐시
  - `search_cases(...)` 시그니처 불변. 기준값은 반올림된 유사도와 비교한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_search.py` 의 import 를 바꾼다:

```python
from pathlib import Path

import pytest

from ai.kb.normalize import normalize
from ai.kb.search import Case, build_index, load_cases, rank, search_cases
from ai.types import AnalysisStatus, CategoryCode
```

`test_search_ignores_draft_case` 의 주석에서 Jaccard 수치를 지운다:

```python
    # CE-9002(draft)와 문면이 거의 같은 질의다. 인덱싱됐다면 유사도 1.0으로
    # 1위에 올라온다. 대신 curated인 CE-9001이 "확인부탁합니다" 어미를 공유해
    # 낮은 점수로 잡히는 것은 정상이다 — 검증 대상은 draft 제외뿐이다.
```

파일 끝에 추가한다:

```python
def _case(case_id, *variants):
    return Case(
        case_id=case_id,
        variants=variants,
        normalized=tuple(normalize(variant) for variant in variants),
        categories=(CategoryCode.DELIVERY,),
    )


def test_ngram_in_every_case_weighs_less_than_ngram_in_one():
    index = build_index(
        (_case("A", "고객님 택배 확인"), _case("B", "고객님 부고 확인"), _case("C", "고객님 과태료 확인"))
    )

    assert index.idf["고객"] < index.idf["부고"]


def test_query_text_missing_from_kb_lowers_the_score():
    # 설계: KB 에 없는 n-gram 도 질의 벡터 크기에 넣는다. 빼면 두 점수가 같아진다.
    index = build_index((_case("A", "부고 안내"),))

    exact = rank(index, "부고 안내")[0].similarity
    padded = rank(index, "부고 안내 오늘 회의 자료")[0].similarity

    assert exact == pytest.approx(1.0)
    assert 0.0 < padded < exact


def test_equal_scores_keep_kb_order():
    index = build_index((_case("A", "택배 확인"), _case("B", "택배 확인")))

    assert [match.case_id for match in rank(index, "택배 확인")] == ["A", "B"]


def test_query_without_searchable_characters_scores_zero(cases_dir):
    # normalize 가 기호를 모두 지우면 질의 벡터가 빈다. 0 으로 나누지 않아야 한다.
    result = search_cases("!!! ???", cases_dir=cases_dir, min_similarity=0.0)

    assert result.status is AnalysisStatus.COMPLETED
    assert [match.similarity for match in result.matches] == [0.0]


def test_threshold_equal_to_reported_similarity_keeps_the_match(cases_dir):
    # 평가 도구는 보고된(반올림된) 유사도로 기준값을 고른다. 운영 검색도 같은 값과
    # 비교해야 평가와 운영이 같은 사례를 남긴다.
    text = "우체국 택배 확인 부탁"
    top = search_cases(text, cases_dir=cases_dir, min_similarity=0.0).matches[0]

    kept = search_cases(text, cases_dir=cases_dir, min_similarity=top.similarity).matches

    assert [match.case_id for match in kept] == [top.case_id]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_search.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_index' from 'ai.kb.search'`

- [ ] **Step 3: 구현**

`ai/src/ai/kb/search.py` 의 import 블록을 아래로 바꾼다:

```python
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
```

`_LOAD_LOCK = threading.Lock()` 바로 아래에 추가한다:

```python

# ai/eval/rag_eval.py 의 dev 절반으로 정하는 값입니다. 바꾸면 평가를 다시 돌립니다.
NGRAM_SIZES: tuple[int, ...] = (2, 3)
SUBLINEAR_TF = False
DEFAULT_MIN_SIMILARITY = 0.3
```

`_trigrams` 와 `_similarity` 함수(주석 `ponytail:` 포함)를 통째로 지우고, 그 자리에 넣는다(`Case` 정의 다음):

```python
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
```

`_load_cases` 함수 다음에 추가한다:

```python
def load_index(cases_dir: Path = CASES_DIR) -> CaseIndex:
    """색인도 프로세스 수명 동안 캐시합니다. load_cases 와 같은 락을 씁니다."""
    with _LOAD_LOCK:
        return _load_index(Path(cases_dir))


@lru_cache(maxsize=8)
def _load_index(cases_dir: Path) -> CaseIndex:
    return build_index(_load_cases(cases_dir))
```

`search_cases` 를 통째로 바꾼다:

```python
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
```

- [ ] **Step 4: 검색 테스트 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_search.py -q`
Expected: `14 passed` (기존 9 + 5)

- [ ] **Step 5: 전체 테스트**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `827 passed`

`test_benign_messages_do_not_match_kb_strongly`(정상 알림이 0.6 이상으로 붙지 않음)가 실패하면 기준을 느슨하게 고치지 않는다. 실패한 문자와 붙은 사례를 기록하고 멈춰서 사용자에게 보고한다.

- [ ] **Step 6: 커밋**

```bash
git add src/ai/kb/search.py tests/test_search.py
git commit -m "feat: 사례 검색을 문자 n-gram TF-IDF 코사인으로 교체" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: dev 조정 → test 1회 확인 → 기본값 확정

**Files:**
- Modify: `ai/eval/rag_eval.py` (`--ngram`·`--tf` 옵션, `parse_ngram`, `make_searcher`)
- Test: `ai/tests/test_rag_eval.py`
- Modify: `ai/src/ai/kb/search.py` (`NGRAM_SIZES`, `SUBLINEAR_TF`, `DEFAULT_MIN_SIMILARITY` 값)
- Create: `ai/eval/reports/<실행일>-rag-tfidf.md`
- Modify: `docs/latency-budget.md` (사례 검색 실측 문장)

**Interfaces:**
- Consumes: Task 6 의 `build_index`, `rank`, `NGRAM_SIZES`, `SUBLINEAR_TF`. Task 2 의 `main`, `TOP_K`.
- Produces (`rag_eval`):
  - `parse_ngram(value: str) -> tuple[int, ...]` — `"3,2"` → `(2, 3)`
  - `make_searcher(cases: Sequence[Case], ngram_sizes: tuple[int, ...], sublinear_tf: bool) -> Searcher`
  - CLI `--ngram 2,3`, `--tf raw|log`. 둘 다 없으면 `default_searcher`(운영 경로)를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_rag_eval.py` 끝에 추가한다:

```python
def test_parse_ngram_sorts_and_dedupes():
    assert rag_eval.parse_ngram("3,2,3") == (2, 3)


def test_make_searcher_uses_given_settings():
    from ai.kb.normalize import normalize
    from ai.kb.search import Case

    cases = [
        Case(
            case_id=f"CE-{i}",
            variants=(text,),
            normalized=(normalize(text),),
            categories=(CategoryCode.DELIVERY,),
        )
        for i, text in enumerate(["부고 안내", "택배 확인", "과태료 납부", "청첩장 도착"])
    ]

    matches = rag_eval.make_searcher(cases, (3,), True)("부고 안내")

    assert len(matches) == rag_eval.TOP_K
    assert matches[0].case_id == "CE-0"
    assert matches[0].similarity == 1.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_eval.py -q`
Expected: FAIL — `AttributeError: module 'rag_eval' has no attribute 'parse_ngram'`

- [ ] **Step 3: 구현**

`ai/eval/rag_eval.py` 의 search import 를 바꾼다:

```python
from ai.kb import search
from ai.kb.search import Case, build_index, load_cases, rank, search_cases
```

`default_searcher` 아래에 추가한다:

```python
def make_searcher(
    cases: Sequence[Case], ngram_sizes: tuple[int, ...], sublinear_tf: bool
) -> Searcher:
    index = build_index(cases, ngram_sizes=ngram_sizes, sublinear_tf=sublinear_tf)
    return lambda text: rank(index, text)[:TOP_K]


def parse_ngram(value: str) -> tuple[int, ...]:
    sizes = tuple(sorted({int(part) for part in value.split(",")}))
    if sizes[0] < 1:
        raise argparse.ArgumentTypeError("n-gram 크기는 1 이상의 정수다")
    return sizes
```

`main` 에서 `--dev-only` 인자 다음에 옵션을 추가한다:

```python
    parser.add_argument("--ngram", type=parse_ngram, help="예: 2,3. 지정하면 그 설정으로 색인을 새로 만든다")
    parser.add_argument("--tf", choices=("raw", "log"), help="TF 가중: 원 빈도 또는 1+ln(tf)")
```

`main` 의 `results = run_search(load_rows(), default_searcher)` 부터 `kb_counts = ...` 까지를 바꾼다:

```python
    cases = load_cases()
    ngram_sizes = args.ngram or search.NGRAM_SIZES
    sublinear_tf = search.SUBLINEAR_TF if args.tf is None else args.tf == "log"
    if args.ngram is None and args.tf is None:
        searcher = default_searcher
    else:
        searcher = make_searcher(cases, ngram_sizes, sublinear_tf)
    results = run_search(load_rows(), searcher)
    kb_counts = Counter(code.value for case in cases for code in case.categories)
```

`meta` 의 `"settings"` 값을 바꾼다:

```python
        "settings": f"문자 n-gram {ngram_sizes} TF-IDF 코사인, TF {'1+ln(tf)' if sublinear_tf else '원 빈도'}",
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_eval.py -q`
Expected: `7 passed`

- [ ] **Step 5: 커밋**

```bash
git add eval/rag_eval.py tests/test_rag_eval.py
git commit -m "feat: RAG 평가에 n-gram·TF 설정 옵션 추가" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: dev 에서 설정 고르기**

```bash
for ngram in 2,3 3 2; do for tf in raw log; do
  echo "== ngram=$ngram tf=$tf"
  PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label grid --dev-only --ngram $ngram --tf $tf
done; done | tee "$SCRATCH/rag-grid.txt"
```

6개 조합 중 하나를 고른다. 기준:
1. dev `smishing.hit3` 가 가장 높은 것
2. 같으면 dev `hard_negative.auc` 가 높은 것
3. 그래도 같으면 위 반복 순서에서 먼저 나온 것

이 단계에서 test 지표를 보지 않는다(`--dev-only` 만 쓴다).

- [ ] **Step 7: 고른 설정을 기본값으로**

`ai/src/ai/kb/search.py` 의 `NGRAM_SIZES` 와 `SUBLINEAR_TF` 를 Step 6 에서 고른 값으로 바꾼다. 예를 들어 `ngram=3 tf=log` 를 골랐다면:

```python
NGRAM_SIZES: tuple[int, ...] = (3,)
SUBLINEAR_TF = True
```

- [ ] **Step 8: test 에서 한 번 측정**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label tfidf`
Expected: 리포트 경로 출력. 리포트의 "설정" 이 Step 7 값과 같고, "기준값" 이 Step 6 출력의 해당 조합 `threshold` 와 같다.

이 실행 뒤에는 설정을 다시 조정하지 않는다.

- [ ] **Step 9: 기준값을 기본값으로**

`ai/src/ai/kb/search.py` 의 `DEFAULT_MIN_SIMILARITY` 를 리포트 "기준값" 의 네 자리 값으로 바꾼다(예: `DEFAULT_MIN_SIMILARITY = 0.2731`).

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `829 passed`. `test_benign_messages_do_not_match_kb_strongly` 가 실패하면 Task 6 Step 5 와 같이 기준을 고치지 말고 멈춰서 보고한다.

- [ ] **Step 10: 해석 섹션 추가**

`<실행일>-rag-tfidf.md` 끝에 손으로 붙인다:

```markdown
## 해석

### 기준선 대비 (test)

| 지표 | jaccard-labelled | tfidf |
|---|---|---|
| 스미싱 hit@3 | <Task 5 리포트 값> | <이 리포트 값> |
| 스미싱 MRR | … | … |
| AUC 스미싱 vs 정상 | … | … |
| AUC 스미싱 vs hard negative | … | … |
| 정상 부착률 | … | … |
| hard negative 부착률 | … | … |
| 질의 p95 | … | … |

### dev 조정 기록

<$SCRATCH/rag-grid.txt 의 6개 조합 dev hit@3·AUC(hard negative)·기준값 표, 고른 조합과 이유>

### 목표 대비

<충족이면 한 줄. 미충족이면 아래를 쓴다>
- 실패 사례 분류: KB 부족(유형 KB 2건 이하) / 표현 차이(뜻은 같은데 글자가 다름) / 라벨 문제 — 실패 사례 표의 각 행을 셋 중 하나로 나누고 건수를 적는다.
- 목표치를 바꾸는 경우: 원래 값, 바꾼 값, 데이터 근거. 원래 기준의 결과(위 운영 지점)는 그대로 둔다.
- 표현 차이가 실패의 다수면 임베딩 검색을 별도 설계로 검토한다(설계 문서의 범위 밖 항목).
```

꺾쇠와 `…` 는 두 리포트와 `rag-grid.txt` 의 실제 값으로 모두 채운다. 빈칸을 남긴 채 커밋하지 않는다.

- [ ] **Step 11: 지연 예산 문서 갱신**

`docs/latency-budget.md` 의 `② 사례 검색` 규칙 문단에서 "검색 자체는 약 2ms지만 프로세스의 첫 호출은 KB 로드가 더해져 170~290ms 걸린다(측정)." 를 리포트 값으로 바꾼다:

```text
검색 자체는 p50 <p50>ms·p95 <p95>ms 지만 프로세스의 첫 호출은 KB 로드와 색인 생성이 더해져 <first>ms 걸린다(`ai/eval/reports/<실행일>-rag-tfidf.md` 측정).
```

꺾쇠는 리포트 "지연" 섹션 값으로 채운다. 문단의 나머지 문장은 바꾸지 않는다.

- [ ] **Step 12: 커밋**

```bash
git add src/ai/kb/search.py eval/reports/*-rag-tfidf.md ../docs/latency-budget.md
git commit -m "feat: RAG 검색 설정과 기준값을 dev 평가로 확정" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

push·PR 은 사용자 확인 후에 한다.
