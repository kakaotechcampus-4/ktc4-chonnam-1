# RAG KB·평가 데이터 분할 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RAG 테스트셋 스미싱을 유형별로 KB : dev : test = 5 : 1 : 1 로 나눠 KB 몫을 KB 레코드로 옮기고, 평가 도구가 데이터의 `split` 필드를 읽도록 바꾼 뒤 새 KB 로 검색 설정·기준값을 다시 정한다.

**Architecture:** 분할은 일회성 스크립트(스크래치, 커밋하지 않음)가 한 번 하고 결과를 데이터(`split` 필드, `CE-0298`~ 레코드)에 남긴다. 데이터 테스트가 배정 규칙(출처 단위, 유형별 dev·test 1건 이상, KB·평가 출처 분리)을 지킨다. 평가 도구는 해시 대신 `split` 필드로 dev/test 를 가르고, 리포트의 hit@3 에 Wilson 95% 구간을 붙인다.

**Tech Stack:** Python ≥3.11 표준 라이브러리(hashlib, json, re, math), 기존 pytest·PyYAML. 새 의존성 없음.

**Spec:** [RAG KB·평가 데이터 분할 설계](../specs/2026-10-03-rag-kb-eval-split-design.md). 선행: [RAG 사례 검색 평가·개선 설계](../specs/2026-10-02-rag-eval-improvement-design.md), [선행 계획](2026-10-03-rag-eval-improvement.md).

## Global Constraints

- 검색 결과는 판정을 바꾸지 않는다. `ai/src/ai/verdict.py`, `ai/src/ai/pipeline/analysis.py`, `ai/src/ai/llm/signals.py` 는 수정하지 않는다.
- 새 의존성 없음. `ai/pyproject.toml` 수정 없음. `ai/` 안에서 `server` import 금지.
- 비율: 유형별 n건이면 dev = test = `max(1, round(n / 7))`, 나머지 KB. 출처(`source`) 단위로 묶는다. 작은 유형부터, 넘치지 않게 채운다.
- 정상·hard negative 는 KB 에 넣지 않는다. 이 행들의 dev/test 는 지금의 템플릿 해시 배정을 그대로 적는다.
- KB 로 옮긴 행의 본문에서 링크 표기를 지운다. 정규화 후 6자 미만이거나 다른 KB 레코드와 같아지면 편입하지 않고 PR 에 남긴다.
- 평가 스미싱 행 중 새 KB 레코드와 top-1 유사도 0.8 이상인 행은 PR 에 남긴다. 사실상 같은 문자면 KB 로 보낸다. 정상·hard negative 는 KB 후보가 아니어서 이 점검에서 뺀다(설계의 "평가 행마다"를 누수 점검 취지대로 스미싱으로 좁혔다).
- 평가 몫은 앞으로도 KB 에 넣지 않는다.
- 분할 스크립트·PR 본문 생성 스크립트는 저장소에 넣지 않는다. 세션 스크래치패드에만 둔다.
- 합성 데이터 경고 문구, 잠정 목표(dev hard negative 부착률 5% 이하, test 스미싱 hit@3 50%)와 변경 규칙은 선행 설계를 따른다.
- 설정값은 dev 에서만 조정한다. 새 test 는 이전과 다른 행이므로 Task 3 에서 한 번만 본다.
- 작업 디렉터리는 `ai/` 다. 테스트·평가 명령:
  ```bash
  PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q
  ```
  현재 `830 passed`.
- 스크래치패드: 셸 상태가 명령 사이에 남지 않으므로 `$SCRATCH` 를 쓰는 Bash 명령마다 앞에 붙인다.
  ```bash
  SCRATCH="C:/Users/ksy/AppData/Local/Temp/claude/C--Users-ksy-OneDrive-------ktc4-chonnam-1/b9aca8f0-e063-4ce8-9053-ba7de9f03060/scratchpad"
  ```
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. push·PR 은 사용자 확인 후에만 한다.
- Task 1 은 `refactor/scenario-message-test-2-ai` 위에 만든 `feature/rag-kb-eval-split` 에서 한다. Task 2·3 은 그 PR 이 머지된 뒤 `refactor/scenario-message-test-2-ai` 에서 한다.
- **최종 리뷰 범위:** 선행 계획의 최종 리뷰가 API 한도로 실행되지 않았다. 이 계획의 최종 리뷰는 `48378a6..HEAD`(두 계획 전부)를 대상으로 하고, 리뷰어에게 두 계획·두 설계와 두 원장(`.superpowers/sdd/2026-10-03-rag-eval-improvement/progress.md`, 이 계획의 원장)을 넘긴다. 리뷰어 호출이 한도로 다시 실패하면 자체 리뷰로 대신하지 않고 멈춰서 사용자에게 보고한다.

## Review Focus

1. 링크 제거가 링크가 아닌 표기(날짜 `2017.11.12`, `No.1`, 금액)까지 지워 KB 본문 뜻이 바뀌는 경우: 지운 결과를 사람이 전부 읽어야 한다. → Task 1 Step 8 에서 58행 전부 대조, PR 본문에 전후 표.
2. YAML 따옴표·역슬래시·`\xa0` 이 든 본문이 frontmatter 파싱에 실패해 레코드가 조용히 검색에서 빠지는 경우: KB 건수가 줄어도 아무도 모른다. → Task 1 `test_every_curated_record_is_indexed`
3. KB 레코드 본문에 링크 표기가 남는 경우: 운영 질의(링크 없음)와 형식이 달라진다. → Task 1 `test_kb_records_have_no_link_text`
4. 같은 게시물 문구가 KB 와 평가에 갈라지는 경우: 자기 자신을 찾아 점수가 부풀려진다. → Task 1 `test_kb_sources_do_not_overlap_rag_testset`, `test_rag_testset_source_stays_in_one_split`
5. test 스미싱이 0건인 평가(데이터를 잘못 고친 경우) 리포트: 구간 계산이 0으로 나누지 않아야 한다. → Task 2 `test_wilson_interval`(n=0)

## 파일 지도

| 파일 | 책임 | Task |
|---|---|---|
| `ai/tests/test_kb_data.py` | 분할 규칙 데이터 테스트 6개 | 1 |
| `ai/eval/datasets/rag_testset.jsonl` | KB 몫 행 삭제, `split` 필드 추가 | 1 |
| `ai/src/ai/kb/case_examples/CE-0298.md` ~ (생성) | KB 로 옮긴 테스트셋 스미싱 | 1 |
| `ai/eval/datasets/README.md` | 스키마·건수·분할 기록 | 1 |
| `ai/eval/rag_metrics.py` | `is_dev` 필드 읽기, `split_key` 출처, `wilson` | 2 |
| `ai/tests/test_rag_metrics.py` | 위 함수 테스트 | 2 |
| `ai/eval/rag_eval.py` | `hits3` 집계, 리포트 구간 표기 | 2 |
| `ai/tests/test_rag_eval.py` | 구간 표기 테스트 | 2 |
| `ai/src/ai/kb/search.py` | 기본 설정·기준값 | 3 |
| `ai/eval/reports/<실행일>-rag-tfidf-split.md` (생성) | 분할 후 리포트 | 3 |
| `docs/latency-budget.md` | 사례 검색 실측 문장 | 3 |

---

### Task 1: 평가셋 분할과 KB 편입 (데이터 PR)

**Files:**
- Modify: `ai/tests/test_kb_data.py`
- Modify: `ai/eval/datasets/rag_testset.jsonl` (스크립트가 다시 씀)
- Create: `ai/src/ai/kb/case_examples/CE-0298.md` 부터 (스크립트가 생성)
- Modify: `ai/eval/datasets/README.md`
- Scratch (커밋 금지): `$SCRATCH/split_rag_testset.py`, `$SCRATCH/force_kb.txt`, `$SCRATCH/near_notes.json`, `$SCRATCH/split_pr.json`, `$SCRATCH/split_pr_body.py`, `$SCRATCH/split_pr_body.md`

**Interfaces:**
- Consumes: `rag_metrics.is_dev(row)` — **이 Task 시점에는 아직 템플릿 해시 버전**이다(Task 2 에서 바뀐다). `ai.kb.normalize.normalize`, `ai.kb.search.{CASES_DIR, Case, build_index, load_cases, rank}`, `ai.types.CategoryCode`.
- Produces:
  - `rag_testset.jsonl` 의 모든 행에 `"split": "dev" | "test"`. 키 순서는 기존 키 뒤에 `split`.
  - KB 레코드 frontmatter 키: `id, status(curated), origin(web_public), source, testset_id, collected_at(2026-10-02), reviewer(rag-testset-2026-10-02), normalized, variants, categories, claimed_brand`.

- [ ] **Step 1: 브랜치 생성**

```bash
git switch refactor/scenario-message-test-2-ai
git switch -c feature/rag-kb-eval-split
```

- [ ] **Step 2: 데이터 테스트 작성**

`ai/tests/test_kb_data.py` 상단 import 에 추가한다:

```python
from collections import Counter, defaultdict
```

`RRN_RE = ...` 줄 바로 위에 상수를 추가한다:

```python
LINK_TEXT_RE = re.compile(r"://|\[\.\]|www\.", re.IGNORECASE)
```

`_load_jsonl` 함수 바로 아래에 추가한다:

```python
def _kb_frontmatter() -> list[dict]:
    import yaml

    metas = []
    for path in sorted(CASES_DIR.glob("CE-*.md")):
        match = re.match(r"\A---\r?\n(.*?)\r?\n---", path.read_text(encoding="utf-8"), re.DOTALL)
        if match is not None:
            metas.append(yaml.safe_load(match.group(1)))
    return metas
```

파일 끝에 추가한다:

```python
def test_every_curated_record_is_indexed():
    # frontmatter 가 YAML 로 안 읽히거나 variants 가 비면 _parse_case 가 조용히
    # 버린다. KB 건수가 줄어도 아무도 모르므로 curated 레코드 수와 맞춘다.
    curated = [meta for meta in _kb_frontmatter() if meta.get("status") == "curated"]

    assert len(load_cases(CASES_DIR)) == len(curated)


def test_kb_records_have_no_link_text():
    # 운영 검색 질의는 링크를 뺀 본문이다. KB 본문에 링크 표기가 남으면 형식이 어긋난다.
    for case in load_cases(CASES_DIR):
        for variant in case.variants:
            assert not LINK_TEXT_RE.search(variant), (case.case_id, variant[:40])


def test_rag_testset_rows_carry_split():
    for row in _load_jsonl("rag_testset.jsonl"):
        assert row["split"] in {"dev", "test"}, row["id"]


def test_rag_testset_source_stays_in_one_split():
    # 같은 게시물의 문구끼리는 비슷하다. dev 와 test 에 갈라지면 누수다.
    splits = defaultdict(set)
    for row in _load_jsonl("rag_testset.jsonl"):
        if row["group"] == "smishing":
            splits[row["source"]].add(row["split"])

    assert {source: values for source, values in splits.items() if len(values) > 1} == {}


def test_rag_testset_keeps_dev_and_test_smishing_per_category():
    counts = Counter(
        (row["category"], row["split"])
        for row in _load_jsonl("rag_testset.jsonl")
        if row["group"] == "smishing"
    )
    for code in CategoryCode:
        if code.value in {"other", "unknown"}:
            continue
        assert counts[(code.value, "dev")] >= 1, code.value
        assert counts[(code.value, "test")] >= 1, code.value


def test_kb_sources_do_not_overlap_rag_testset():
    # KB 로 옮긴 출처가 평가에 남으면 같은 게시물 문구로 자기 자신을 찾는다.
    kb_sources = {meta.get("source") for meta in _kb_frontmatter()} - {None}
    eval_sources = {row["source"] for row in _load_jsonl("rag_testset.jsonl") if row["source"]}

    assert kb_sources & eval_sources == set()
```

- [ ] **Step 3: 테스트 실행**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_kb_data.py -q`
Expected: `3 failed` — `test_rag_testset_rows_carry_split`, `test_rag_testset_source_stays_in_one_split`, `test_rag_testset_keeps_dev_and_test_smishing_per_category` 모두 `KeyError: 'split'`. 나머지 새 테스트 3개(indexed, no_link_text, sources_do_not_overlap)는 지금 데이터에서도 지켜지므로 통과한다. 기존 테스트도 통과한다.

- [ ] **Step 4: 분할 스크립트 작성 (스크래치, 커밋 금지)**

`$SCRATCH/split_rag_testset.py`:

```python
"""일회성: rag_testset 스미싱을 유형별로 KB:dev:test = 5:1:1 로 나눈다.

ai/ 에서 실행한다. 저장소에 커밋하지 않는다.

    python split_rag_testset.py --dry-run [--force-kb ids.txt]    # 배정만 출력
    python split_rag_testset.py --out pr.json [--force-kb ids.txt]  # 파일을 바꾼다
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "eval")
sys.path.insert(0, "src")

import rag_metrics  # noqa: E402  (해시 is_dev 로 정상 문자 배정을 보존한다. Task 2 전에 돌린다)
from ai.kb.normalize import normalize  # noqa: E402
from ai.kb.search import CASES_DIR, Case, build_index, load_cases, rank  # noqa: E402
from ai.types import CategoryCode  # noqa: E402

TESTSET = Path("eval/datasets/rag_testset.jsonl")
FIRST_ID = 298
MIN_CHARS = 6
NEAR_DUP = 0.8
CATEGORIES = [code.value for code in CategoryCode if code.value not in {"other", "unknown"}]
# 스킴 뒤 한글은 도메인 라벨(뒤에 . 이나 [ 가 올 때)만 링크로 본다. 조사는 남긴다.
LINK_RE = re.compile(
    r"(?:h[tx]{2}ps?|httpx)://(?:[^\s가-힣]|[가-힣]+(?=[.\[]))+"
    r"|www\.[!-~]+"
    r"|[A-Za-z0-9*\-]+(?:(?:\[\.\]|\.)[A-Za-z0-9*\-]+)*(?:\[\.\]|\.)[A-Za-z*][A-Za-z0-9*\-]*(?:/[!-~]*)?"
)


def strip_links(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", LINK_RE.sub(" ", text)).strip()


def order(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def assign(smishing: list[dict], forced_kb: set[str]) -> dict[str, str]:
    """출처별 몫(kb/dev/test)을 정한다. 같은 출처는 같은 몫이다."""
    bucket = {row["source"]: "kb" for row in smishing if row["id"] in forced_kb}
    sizes = Counter(row["category"] for row in smishing)
    # 작은 유형부터 정한다. 여러 유형에 걸친 출처는 먼저 처리한 유형이 몫을 정하므로
    # 건수가 적어 한 묶음에도 크게 흔들리는 유형이 먼저 고르게 한다.
    for category in sorted(sizes, key=lambda code: (sizes[code], CATEGORIES.index(code))):
        rows = [row for row in smishing if row["category"] == category]
        target = max(1, round(len(rows) / 7))
        per_source = Counter(row["source"] for row in rows)
        counts = Counter()
        for source, n in per_source.items():
            if source in bucket:
                counts[bucket[source]] += n
        for source in sorted(per_source, key=order):
            if source in bucket:
                continue
            n = per_source[source]
            # 넘치게 채우지 않는다. 묶음이 커서 안 들어가면 다음 몫으로 보낸다.
            if counts["test"] + n <= target:
                bucket[source] = "test"
            elif counts["dev"] + n <= target:
                bucket[source] = "dev"
            else:
                bucket[source] = "kb"
            counts[bucket[source]] += n
    return bucket


def quote(value: str) -> str:
    # JSON 문자열은 그대로 YAML 큰따옴표 스칼라다.
    return json.dumps(value, ensure_ascii=False)


def frontmatter(case_id: str, row: dict, text: str) -> str:
    return "\n".join([
        "---",
        f"id: {case_id}",
        "status: curated",
        "origin: web_public",
        f"source: {quote(row['source'])}",
        f"testset_id: {row['id']}",
        "collected_at: 2026-10-02",
        "reviewer: rag-testset-2026-10-02",
        f"normalized: {quote(normalize(text))}",
        "variants:",
        f"  - {quote(text)}",
        f"categories: [{row['category']}]",
        'claimed_brand: ""',
        "---",
        "",
        "공개 웹 원문. RAG 테스트셋에서 KB 몫으로 옮겼고 링크 표기를 지웠다.",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", help="PR 본문 재료(JSON)를 쓸 경로")
    parser.add_argument("--force-kb", help="KB 로 보낼 테스트셋 id 목록 파일(줄마다 하나)")
    args = parser.parse_args()

    rows = [json.loads(line) for line in TESTSET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any("split" in row for row in rows):
        sys.exit("이미 split 필드가 있다. 두 번 돌리지 않는다.")
    if (CASES_DIR / f"CE-{FIRST_ID:04d}.md").exists():
        sys.exit(f"CE-{FIRST_ID:04d} 가 이미 있다.")
    forced = set()
    if args.force_kb:
        forced = {line.strip() for line in Path(args.force_kb).read_text(encoding="utf-8").splitlines() if line.strip()}

    smishing = [row for row in rows if row["group"] == "smishing"]
    bucket = assign(smishing, forced)

    existing = {value for case in load_cases() for value in case.normalized}
    seen: set[str] = set()
    kb_rows, skipped, stripped = [], [], []
    for row in smishing:
        if bucket[row["source"]] != "kb":
            continue
        text = strip_links(row["text"])
        normalized = normalize(text)
        if text != row["text"]:
            stripped.append({"id": row["id"], "before": row["text"], "after": text})
        if len(normalized) < MIN_CHARS:
            skipped.append({"id": row["id"], "reason": f"링크를 지우면 {len(normalized)}자", "text": row["text"]})
        elif normalized in existing or normalized in seen:
            skipped.append({"id": row["id"], "reason": "링크를 지우면 다른 KB 레코드와 같다", "text": row["text"]})
        else:
            seen.add(normalized)
            kb_rows.append((row, text))

    eval_rows = []
    for row in rows:
        if row["group"] == "smishing":
            if bucket[row["source"]] == "kb":
                continue
            split = bucket[row["source"]]
        else:
            split = "dev" if rag_metrics.is_dev(row) else "test"
        eval_rows.append({**row, "split": split})

    table = defaultdict(Counter)
    for row in smishing:
        table[row["category"]][bucket[row["source"]]] += 1
    for item in skipped:
        category = next(row["category"] for row in smishing if row["id"] == item["id"])
        table[category]["kb"] -= 1
        table[category]["excluded"] += 1

    new_cases = [
        Case(
            case_id=f"CE-{FIRST_ID + i:04d}",
            variants=(text,),
            normalized=(normalize(text),),
            categories=(CategoryCode(row["category"]),),
        )
        for i, (row, text) in enumerate(kb_rows)
    ]
    index = build_index(new_cases)
    near = []
    for row in eval_rows:
        if row["group"] != "smishing":
            continue
        top = rank(index, row["text"])[0]
        if top.similarity >= NEAR_DUP:
            near.append({"id": row["id"], "similarity": top.similarity, "text": row["text"],
                         "case_id": top.case_id, "case_text": top.matched_variant})

    summary = {
        "table": {category: dict(table[category]) for category in CATEGORIES},
        "kb_new": len(kb_rows),
        "stripped": stripped,
        "skipped": skipped,
        "near_duplicates": near,
        "forced_kb": sorted(forced),
    }
    for category in CATEGORIES:
        counts = table[category]
        print(f"{category:28s} kb={counts['kb']:2d} dev={counts['dev']} test={counts['test']} excluded={counts['excluded']}")
    print("kb_new", len(kb_rows), "stripped", len(stripped), "skipped", len(skipped), "near_duplicates", len(near))
    for item in skipped:
        print("SKIP", item["id"], item["reason"], "|", item["text"])
    for item in near:
        print("NEAR", item["id"], item["similarity"], "|", item["text"], "|", item["case_id"], item["case_text"])
    if args.dry_run:
        return

    for case, (row, text) in zip(new_cases, kb_rows):
        (CASES_DIR / f"{case.case_id}.md").write_text(frontmatter(case.case_id, row, text), encoding="utf-8")
    TESTSET.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in eval_rows), encoding="utf-8")
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 미리 보기**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python "$SCRATCH/split_rag_testset.py" --dry-run`
Expected (2026-10-03 계획 작성 때 확인한 값):
- 유형별: 20건 유형 7개는 `kb=14 dev=3 test=3`(delivery 만 `kb=13 … excluded=1`), account_security `12/2/2`, prize_or_event `11/2/2`, public_support `10/2/2`, acquaintance_impersonation·obituary `7/1/1`, telecom_refund `5/1/1`, public_refund `3/1/1`.
- `kb_new 152 stripped 58 skipped 1 near_duplicates 3`
- `SKIP S-delivery-02 링크를 지우면 다른 KB 레코드와 같다`
- `NEAR` 3줄: `S-health_check-09`, `S-public_support-04`, `S-public_support-06`

값이 다르면 멈추고 원인을 찾는다(데이터가 바뀌었거나 스크립트가 계획과 다르다).

- [ ] **Step 6: 근접 중복 판단**

`NEAR` 줄마다 평가 문구와 KB 문구를 읽고 정한다.
- **같은 문자**: 띄어쓰기·기호·링크·숫자(날짜·금액)만 다르고 문장이 같다. → 평가 id 를 `$SCRATCH/force_kb.txt` 에 한 줄씩 적는다.
- **다른 문자**: 같은 캠페인이라도 문장이 다르다. → 평가에 둔다.

결정마다 `$SCRATCH/near_notes.json` 에 `{"<평가 id>": "<한 문장 이유>"}` 로 적는다(두 경우 모두).

`force_kb.txt` 에 적은 것이 있으면 미리 보기를 다시 돌린다:

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python "$SCRATCH/split_rag_testset.py" --dry-run --force-kb "$SCRATCH/force_kb.txt"`

새 `NEAR` 줄이 생기면 같은 기준으로 판단하고 반복한다. 모든 유형이 dev·test 1건 이상인지 확인한다. 1건 미만이 되면 그 강제 배정을 빼고 평가에 둔 채 이유를 적는다. `force_kb.txt` 가 비어 있으면 아래 명령에서 `--force-kb` 를 빼고, `near_notes.json` 은 그래도 만든다.

- [ ] **Step 7: 실행**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python "$SCRATCH/split_rag_testset.py" --out "$SCRATCH/split_pr.json" --force-kb "$SCRATCH/force_kb.txt"`
Expected: Step 6 마지막 미리 보기와 같은 출력. 이후:

```bash
git status --porcelain | head -3
git ls-files --others --exclude-standard src/ai/kb/case_examples | wc -l
wc -l eval/datasets/rag_testset.jsonl
```
Expected: `M ai/eval/datasets/rag_testset.jsonl`, 새 CE 파일 수 = `kb_new`, testset 행 수 = 775 − 215 + (dev+test 스미싱 합). 강제 배정이 없으면 새 CE 152개, testset 622행.

- [ ] **Step 8: 링크를 지운 결과 전수 확인**

```bash
PYTHONUTF8=1 python -c "
import json
d = json.load(open(r'$SCRATCH/split_pr.json', encoding='utf-8'))
for item in d['stripped']: print(item['id'], '|', item['before'], '=>', item['after'])
" > "$SCRATCH/stripped_review.txt"
```

`stripped_review.txt` 를 Read 로 전부 읽는다. 행마다 확인한다: 링크가 아닌 표기(날짜, 금액, 버전, 문장 부호)가 지워지지 않았는가, 남은 문장이 원문 용건을 유지하는가. 잘못 지운 행이 있으면:

```bash
git checkout -- eval/datasets/rag_testset.jsonl
git clean -f -- src/ai/kb/case_examples
```

`LINK_RE` 를 고치고 Step 5 부터 다시 한다. 고친 내용을 원장에 Ruling 으로 남긴다.

새 레코드 3개(`CE-0298.md`, 가운데 하나, 마지막 하나)를 Read 로 열어 frontmatter 형식이 위 Interfaces 와 같은지 본다.

- [ ] **Step 9: 테스트 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `836 passed` (830 + 6). 실패하면 원인을 고치고 기준을 느슨하게 하지 않는다. 특히:
- `test_eval_dataset_is_disjoint_from_kb` 실패: 링크를 지운 KB 본문이 평가 행 원문과 같아졌다. 그 평가 id 를 `force_kb.txt` 에 넣고 Step 8 의 되돌리기 명령 뒤 Step 6 부터 다시 한다.
- `test_benign_messages_do_not_match_kb_strongly` 실패: 멈추고 사용자에게 보고한다(계획 작성 때 새 KB 로 잰 최고값은 0.3487).

- [ ] **Step 10: README 갱신**

`ai/eval/datasets/README.md` 에서 스키마 블록의

```
 "template": null | "<템플릿 ID>"}
```

를 아래로 바꾼다:

```
 "template": null | "<템플릿 ID>", "split": "dev" | "test"}
```

표의

```
| `smishing` | 실물 스미싱 | 공개 웹 원문 (`source` 필수) | 215 |
```

를 아래로 바꾼다(62 는 Step 7 의 dev+test 스미싱 합):

```
| `smishing` | 실물 스미싱 | 공개 웹 원문 (`source` 필수) | 62 (원래 215, 아래 분할 참조) |
```

`- KB 와 겹치는 원문 38건은 제외했다(...)` 줄 다음, `## 현재 한계` 앞에 넣는다. 숫자는 `split_pr.json` 의 `kb_new`·`skipped` 건수와 맞춘다:

```markdown

### KB·평가 분할 (2026-10-03)

스미싱 215건을 유형별로 KB : dev : test = 5 : 1 : 1 로 나눴다
([설계](../../../docs/superpowers/specs/2026-10-03-rag-kb-eval-split-design.md)).

- KB 몫 152건은 링크 표기를 지워 KB 레코드(`CE-0298`~)로 옮겼다. 레코드의 `testset_id` 가 원래 id 다.
  링크를 지우자 다른 행과 같아진 1건은 어느 쪽에도 넣지 않았다.
- 남은 행에 `split` 을 붙였다. 스미싱은 출처(`source`) 단위로 묶어 배정했고, 정상·hard negative 는
  기존 템플릿 해시 배정을 그대로 적었다.
- 평가 몫은 앞으로도 KB 에 넣지 않는다.
```

- [ ] **Step 11: 커밋 (평가셋 1개 + 유형별 KB)**

```bash
git add tests/test_kb_data.py eval/datasets/rag_testset.jsonl eval/datasets/README.md
git commit -q -m "feat(eval): RAG 테스트셋에 split 필드 추가하고 KB 몫 행 분리" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
for category in delivery address_correction payment penalty card_or_account public_refund public_support acquaintance_impersonation invitation obituary prize_or_event health_check telecom_refund account_security; do
  files=$(grep -l "^categories: \[$category\]" $(git ls-files --others --exclude-standard src/ai/kb/case_examples) || true)
  [ -n "$files" ] || continue
  count=$(echo "$files" | wc -l)
  git add $files
  git commit -q -m "feat(kb): RAG 테스트셋 $category 사례 ${count}건 편입" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  echo "$category $count"
done
git status --porcelain
```
Expected: 유형별 건수가 Step 7 표의 kb 열과 같고, 마지막 `git status` 출력이 없다.

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `836 passed`

- [ ] **Step 12: PR 본문 생성**

`$SCRATCH/split_pr_body.py`:

```python
"""split_pr.json 으로 PR 본문을 만든다."""

import json
import sys
from pathlib import Path

scratch = Path(sys.argv[1])
data = json.loads((scratch / "split_pr.json").read_text(encoding="utf-8"))
notes = json.loads((scratch / "near_notes.json").read_text(encoding="utf-8"))


def cell(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


table = data["table"]
lines = [
    "## 변경 요약",
    "",
    f"RAG 테스트셋 스미싱 215건을 유형별로 KB : dev : test = 5 : 1 : 1 로 나눴다. KB 몫 {data['kb_new']}건을 "
    "KB 레코드(`CE-0298`~)로 옮기고, 남은 평가 행에 `split` 필드를 붙였다. 정상·hard negative 는 KB 에 넣지 않았고 "
    "dev/test 배정도 바뀌지 않았다.",
    "",
    "설계: `docs/superpowers/specs/2026-10-03-rag-kb-eval-split-design.md`",
    "",
    "## 리뷰 방법",
    "",
    "- 첫 커밋: 평가셋(`split` 필드, KB 몫 행 삭제), 데이터 테스트, README.",
    "- 나머지 커밋: 유형별 KB 레코드. 유형 라벨은 테스트셋 것을 그대로 썼다. 아래 \"링크 표기를 지운 행\" 표에서 "
    "지운 뒤 문장이 원문 용건을 유지하는지 봐 주세요.",
    "",
    "## 유형별 배정",
    "",
    "| 유형 | KB | dev | test | 제외 |",
    "|---|---|---|---|---|",
    *(
        f"| {code} | {c.get('kb', 0)} | {c.get('dev', 0)} | {c.get('test', 0)} | {c.get('excluded', 0)} |"
        for code, c in table.items()
    ),
    "",
    f"## 링크 표기를 지운 행 ({len(data['stripped'])}건)",
    "",
    "| testset id | 지우기 전 | 지운 뒤 |",
    "|---|---|---|",
    *(f"| {s['id']} | {cell(s['before'])} | {cell(s['after'])} |" for s in data["stripped"]),
    "",
    f"## 편입하지 않은 행 ({len(data['skipped'])}건)",
    "",
    "| testset id | 이유 | 원문 |",
    "|---|---|---|",
    *(f"| {s['id']} | {s['reason']} | {cell(s['text'])} |" for s in data["skipped"]),
    "",
    "## 근접 중복 (평가 행 ↔ 새 KB 레코드, 유사도 0.8 이상)",
    "",
    f"KB 로 강제 배정: {', '.join(data['forced_kb']) or '없음'}",
    "",
    *(f"- {case_id}: {notes[case_id]}" for case_id in data["forced_kb"]),
    "",
    "평가에 둔 행:",
    "",
    "| 평가 id | 유사도 | 평가 문구 | KB 문구 | 이유 |",
    "|---|---|---|---|---|",
    *(
        f"| {n['id']} | {n['similarity']:.4f} | {cell(n['text'])} | {n['case_id']} {cell(n['case_text'])} | {notes[n['id']]} |"
        for n in data["near_duplicates"]
    ),
    "",
    "## 머지 전 주의",
    "",
    "평가 몫은 앞으로도 KB 에 넣지 않는다. 이 분할로 낸 평가 수치는 머지 전까지 근거로 쓰지 않는다.",
    "",
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
]
(scratch / "split_pr_body.md").write_text("\n".join(lines), encoding="utf-8")
```

Run: `PYTHONUTF8=1 python "$SCRATCH/split_pr_body.py" "$SCRATCH"`
Expected: `$SCRATCH/split_pr_body.md` 생성. `near_notes.json` 에 강제 배정 id 와 평가에 둔 근접 중복 id 가 모두 있어야 한다(없으면 `KeyError` 로 멈춘다).

- [ ] **Step 13: 사용자 확인 후 push·PR**

사용자에게 push 와 PR base 를 묻는다. 권장: `refactor/scenario-message-test-2-ai` 를 먼저 push(선행 계획 Task 5~7 커밋과 이 계획 커밋)하고, `feature/rag-kb-eval-split` 를 push 해 그 브랜치로 PR. 확인 뒤:

```bash
git push -q origin refactor/scenario-message-test-2-ai
git push -q -u origin feature/rag-kb-eval-split
gh pr create --base refactor/scenario-message-test-2-ai --head feature/rag-kb-eval-split --title "feat(kb): RAG 테스트셋 스미싱을 KB·평가로 5:2 분할" --body-file "$SCRATCH/split_pr_body.md"
```

**체크포인트:** PR 이 머지될 때까지 Task 2 로 넘어가지 않는다.

---

### Task 2: 평가 도구가 split 필드를 읽고 구간을 표기

**Files:**
- Modify: `ai/eval/rag_metrics.py`
- Modify: `ai/tests/test_rag_metrics.py`
- Modify: `ai/eval/rag_eval.py`
- Modify: `ai/tests/test_rag_eval.py`

**Interfaces:**
- Consumes: Task 1 의 `row["split"]`, `row["source"]`.
- Produces:
  - `rag_metrics.split_key(row) -> str` — `template` → `source` → `id` 순
  - `rag_metrics.is_dev(row) -> bool` — `row["split"] == "dev"`. `split` 이 없으면 `KeyError`
  - `rag_metrics.wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]` — n=0 이면 `(0.0, 0.0)`
  - `rag_eval.summarize` 의 스미싱 항목에 `"hits3": int` 추가
  - 리포트 운영 지점 줄: `- test 스미싱 hit@3: {pct} ({hits}/{n}, 95% 구간 {low}~{high}) — 잠정 목표 …`

- [ ] **Step 1: 머지된 브랜치로 이동**

```bash
git switch refactor/scenario-message-test-2-ai
git pull
PYTHONUTF8=1 python -c "
import json
rows = [json.loads(l) for l in open('eval/datasets/rag_testset.jsonl', encoding='utf-8') if l.strip()]
print(len(rows), all('split' in r for r in rows))"
```
Expected: Task 1 Step 7 의 행 수와 `True`.

- [ ] **Step 2: 실패하는 테스트 작성**

`ai/tests/test_rag_metrics.py` 에서 `test_rows_from_one_template_land_in_the_same_half`, `test_rows_without_template_split_by_id`, `test_split_is_roughly_half_and_stable` 세 함수를 지우고 그 자리에 넣는다:

```python
def test_is_dev_reads_the_split_field():
    row = {"id": "S-x-01", "template": None, "source": "https://a"}

    assert metrics.is_dev({**row, "split": "dev"})
    assert not metrics.is_dev({**row, "split": "test"})


def test_split_key_prefers_template_then_source_then_id():
    assert metrics.split_key({"id": "B-penalty-01", "template": "B-penalty-t2", "source": None}) == "B-penalty-t2"
    assert metrics.split_key({"id": "S-penalty-01", "template": None, "source": "https://a"}) == "https://a"
    assert metrics.split_key({"id": "S-penalty-02", "template": None, "source": None}) == "S-penalty-02"
```

파일 끝에 추가한다:

```python
def test_wilson_interval():
    low, high = metrics.wilson(6, 31)

    assert (round(low, 4), round(high, 4)) == (0.0919, 0.3628)
    assert metrics.wilson(0, 10)[0] == pytest.approx(0.0, abs=1e-12)
    assert metrics.wilson(31, 31)[1] == pytest.approx(1.0, abs=1e-12)
    assert metrics.wilson(0, 0) == (0.0, 0.0)
```

`ai/tests/test_rag_eval.py` 의 `test_report_flags_unlabelled_kb_and_escapes_cells` 바로 아래에 추가한다:

```python
def test_report_shows_hit3_interval():
    evaluation = _evaluate()
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(evaluation, meta)

    assert evaluation["test"]["smishing"]["hits3"] == 1
    assert "test 스미싱 hit@3: 50.0% (1/2, 95% 구간 9.5%~90.5%)" in report
```

- [ ] **Step 3: 실패 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_metrics.py tests/test_rag_eval.py -q`
Expected: `4 failed` — `test_is_dev_reads_the_split_field`(같은 id 로 dev·test 를 다르게 못 냄), `test_split_key_prefers_template_then_source_then_id`(source 대신 id), `test_wilson_interval`(`AttributeError: … 'wilson'`), `test_report_shows_hit3_interval`(`KeyError: 'hits3'`).

- [ ] **Step 4: 구현**

`ai/eval/rag_metrics.py`:
- `import hashlib` 을 `import math` 로 바꾼다.
- `split_key`, `is_dev` 를 아래로 바꾼다:

```python
def split_key(row: dict) -> str:
    # 합성 정상 문자는 같은 템플릿끼리, 스미싱은 같은 게시물끼리 비슷하다.
    # 이 단위로 묶어 dev 와 test 에 나눴다. 배정은 데이터의 split 필드에 있다.
    return row.get("template") or row.get("source") or row["id"]


def is_dev(row: dict) -> bool:
    return row["split"] == "dev"
```

- 파일 끝에 추가한다:

```python
def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """비율의 95% 신뢰구간(Wilson). 표본이 작아도 0~1 을 벗어나지 않는다."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))
```

`ai/eval/rag_eval.py` 의 `summarize` 에서 스미싱 항목을 바꾼다:

```python
        if group == "smishing":
            ranks = [_rank(result, threshold) for result in part]
            entry |= {
                "hit1": metrics.hit_rate(ranks, 1),
                "hit3": metrics.hit_rate(ranks, 3),
                "hits3": sum(1 for rank in ranks if rank is not None and rank <= 3),
                "mrr": metrics.mrr(ranks),
            }
```

`render_report` 앞부분의

```python
    hit3 = evaluation["test"]["smishing"]["hit3"]
```

를 아래로 바꾼다:

```python
    smishing = evaluation["test"]["smishing"]
    hit3 = smishing["hit3"]
    # test 스미싱이 수십 건이라 한 건이 수 %p 다. 목표 대비 판단에 구간 폭이 필요하다.
    low, high = metrics.wilson(smishing["hits3"], smishing["n"])
```

운영 지점의 hit@3 줄을 바꾼다:

```python
        f"- test 스미싱 hit@3: {_pct(hit3)} ({smishing['hits3']}/{smishing['n']}, 95% 구간 {_pct(low)}~{_pct(high)}) — 잠정 목표 {_pct(TARGET_HIT_AT_3)} {verdict}",
```

- [ ] **Step 5: 통과 확인**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests/test_rag_metrics.py tests/test_rag_eval.py -q`
Expected: `18 passed` (metrics 9, eval 9)

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `837 passed`

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label check --dev-only`
Expected: JSON 출력. `dev.smishing.n` 이 31(강제 배정이 있었다면 Task 1 Step 7 의 dev 스미싱 합). 리포트 파일은 생기지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add eval/rag_metrics.py eval/rag_eval.py tests/test_rag_metrics.py tests/test_rag_eval.py
git commit -q -m "feat(eval): RAG 평가가 split 필드로 dev/test 를 나누고 hit@3 구간을 표기" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 새 KB 로 설정·기준값 재조정

**Files:**
- Modify: `ai/src/ai/kb/search.py` (`NGRAM_SIZES`, `SUBLINEAR_TF`, `DEFAULT_MIN_SIMILARITY` 값)
- Create: `ai/eval/reports/<실행일>-rag-tfidf-split.md`
- Modify: `docs/latency-budget.md`

**Interfaces:**
- Consumes: `python eval/rag_eval.py --label <L> [--dev-only] [--ngram 2,3] [--tf raw|log]` (선행 계획), Task 2 의 구간 표기.
- Produces: 확정된 검색 기본값과 리포트.

- [ ] **Step 1: dev 에서 6개 조합 비교**

```bash
for ngram in 2,3 3 2; do for tf in raw log; do
  echo "== ngram=$ngram tf=$tf"
  PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label grid --dev-only --ngram $ngram --tf $tf
done; done > "$SCRATCH/rag-grid-split.txt" 2>&1
PYTHONUTF8=1 python - "$SCRATCH/rag-grid-split.txt" <<'EOF'
import json, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
for head, body in re.findall(r"== (.+)\n(\{.*?\n\})", text, re.S):
    d = json.loads(body); dev = d["dev"]
    print(f"{head:20s} thr={d['threshold']:.4f} hits3={dev['smishing']['hits3']}/{dev['smishing']['n']} hit3={dev['smishing']['hit3']:.3f} hit1={dev['smishing']['hit1']:.3f} mrr={dev['smishing']['mrr']:.3f} aucB={dev['benign']['auc']:.3f} aucHN={dev['hard_negative']['auc']:.3f}")
EOF
```

Expected: 6줄. 고르는 규칙(선행 계획과 같음):
1. dev `hit3` 최고
2. 같으면 dev hard negative AUC 최고
3. 그래도 같으면 위 반복 순서에서 먼저 나온 것

이 단계에서 test 지표를 보지 않는다.

- [ ] **Step 2: 고른 설정을 기본값으로**

`ai/src/ai/kb/search.py` 의 `NGRAM_SIZES`, `SUBLINEAR_TF` 를 Step 1 에서 고른 값으로 바꾼다. 현재 값은 `(2,)`, `True` 다. 같으면 바꾸지 않고 커밋도 하지 않는다. 바꿨다면:

```bash
git add src/ai/kb/search.py
git commit -q -m "feat: 분할 후 dev 평가로 사례 검색 n-gram·TF 설정 변경" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

리포트에 깨끗한 커밋이 찍히도록 `git status --porcelain` 출력이 없는지 확인한다.

- [ ] **Step 3: test 에서 한 번 측정**

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python eval/rag_eval.py --label tfidf-split`
Expected: 리포트 경로 출력. 리포트의 커밋에 `+dirty` 가 없다. "설정" 이 Step 2 값과 같고, "기준값" 이 Step 1 출력의 그 조합 `thr` 와 같다. 운영 지점 줄에 `95% 구간` 이 있다.

이 실행 뒤에는 설정을 다시 조정하지 않는다.

- [ ] **Step 4: 기준값을 기본값으로**

`ai/src/ai/kb/search.py` 의 `DEFAULT_MIN_SIMILARITY` 를 리포트 "기준값" 의 네 자리 값으로 바꾼다(현재 `0.2039`. 같으면 그대로 둔다).

Run: `PYTHONUTF8=1 PYTHONPATH="src;.venv/Lib/site-packages" python -m pytest tests -q`
Expected: `837 passed`. `test_benign_messages_do_not_match_kb_strongly` 가 실패하면 기준을 고치지 말고 멈춰서 보고한다.

- [ ] **Step 5: 해석 섹션 추가**

`<실행일>-rag-tfidf-split.md` 끝에 붙인다. 꺾쇠와 `…` 는 이 리포트, `rag-grid-split.txt`, `split_pr.json` 의 실제 값으로 모두 채운다. 빈칸을 남긴 채 커밋하지 않는다.

```markdown
## 해석

### 이전 리포트와의 관계

KB 와 평가 행이 모두 바뀌었다. KB 는 296건에서 <296 + kb_new>건이 됐고, 평가 스미싱은 215건에서 <dev+test>건(dev <n> / test <n>)이 됐다. 그래서 `2026-10-03-rag-tfidf.md` 등 이전 리포트 수치와 직접 비교하지 않는다. 분할 방법은 `docs/superpowers/specs/2026-10-03-rag-kb-eval-split-design.md` 를 따른다.

### 이번 결과 (test)

| 지표 | 값 |
|---|---|
| 스미싱 hit@3 | <pct> (<hits>/<n>, 95% 구간 <low>~<high>) |
| 스미싱 MRR | … |
| AUC 스미싱 vs 정상 / vs hard negative | … / … |
| 정상 / hard negative 부착률 | … / … |
| 질의 p95 · 첫 호출 | … · … |

### dev 조정 기록

| n-gram | TF | 기준값 | hits@3 | hit@1 | MRR | AUC 정상 | AUC hard negative |
|---|---|---|---|---|---|---|---|
| … 6행 … |

<고른 조합과 이유. dev 스미싱이 <n>건뿐이라 1~2건 차이는 우열로 보지 않는다는 점을 적는다>

### 목표 대비

<구간 상단이 50% 미만이면 "미달", 구간 하단이 50% 이상이면 "충족", 50% 가 구간 안이면 "판단 보류(표본 부족)" 로 쓴다>

- 실패 사례 표의 각 행을 넷으로 나눠 건수를 적는다: KB 부족(유형별 표의 KB 열 2건 이하) / 표현 차이 / 라벨 문제 / 기준값 미달(top-1 유형은 맞음).
- 목표치는 선행 설계의 변경 규칙을 따른다. 바꾸면 원래 값·바꾼 값·데이터 근거를 적는다.
- 표현 차이가 실패의 다수면 임베딩 검색을 별도 설계로 검토한다는 점을 적는다. 아니면 그렇지 않다고 적는다.
```

- [ ] **Step 6: 지연 예산 문서 갱신**

`docs/latency-budget.md` 의

```text
검색 자체는 p50 1.96ms·p95 2.99ms 지만
  프로세스의 첫 호출은 KB 로드와 색인 생성이 더해져 245.3ms 걸린다(`ai/eval/reports/2026-10-03-rag-tfidf.md` 측정).
```

를 리포트 "지연" 섹션 값으로 바꾼다:

```text
검색 자체는 p50 <p50>ms·p95 <p95>ms 지만
  프로세스의 첫 호출은 KB 로드와 색인 생성이 더해져 <first>ms 걸린다(`ai/eval/reports/<실행일>-rag-tfidf-split.md` 측정).
```

- [ ] **Step 7: 커밋**

```bash
git add src/ai/kb/search.py eval/reports/*-rag-tfidf-split.md ../docs/latency-budget.md
git commit -q -m "feat: KB·평가 분할 후 RAG 검색 기준값 확정" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

push·PR 은 사용자 확인 후에 한다.
