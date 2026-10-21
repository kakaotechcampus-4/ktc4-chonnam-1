# RAG 평가셋 확장과 안내 평가 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 평가셋에 정답 업무 라벨과 새 문자 세 종류(36건)를 더하고, `find_guidance()` 안내가 맞았는지 재는 `guidance_eval.py` 와 종류별 검색 표를 만들어 리포트를 낸다.

**Architecture:** `rag_testset.jsonl` 한 파일에 `expected_business`(전 행)·`kind`·`expected_actions`(새 행) 칸을 더한다. 새 `ai/eval/guidance_eval.py` 가 각 행을 고정 규칙(주제·택배사·요구 행동 규칙)으로 분석해 `find_guidance()` 에 넣고 맞음/빠짐/엉뚱함/맞게 없음으로 나눈다. `rag_eval.py` 는 종류별 표만 더한다. 새 문자는 공식 출처 예시를 먼저 넣고, 합성 후보는 사용자 검수 뒤에만 넣는다.

**Tech Stack:** Python 3.12, pytest, PyYAML

**Spec:** `docs/superpowers/specs/2026-10-11-eval-expansion-design.md`

## Global Constraints

- 작업 위치: worktree `C:\Users\ksy\OneDrive\바탕 화면\ktc4-chonnam-1-rag`, 브랜치 `feature/rag-fix-ai`. 태스크마다 커밋하고 푸시한다. PR 은 만들지 않는다.
- 평가는 LLM 없이 고정 규칙만 쓴다. `ai/verdict.py`, `ai/src/ai/types.py`, `Topic` 값, `ai/src/ai/kb/guidance.py`, `ai/src/ai/taxonomy.py` 를 바꾸지 않는다.
- `expected_business` 값: `delivery` / `penalty` / `overseas_payment` / `none`.
- `kind` 값: `official_notice` / `same_pretext` / `unclear_org`. 새 행 id: `K-<kind>-<business>-NN`.
- `expected_actions`: `MessageDoubt` 값 목록(`unknown` 제외, 빈 목록 허용). 새 행에만.
- `origin` 값: `web_public` / `synthetic_template` / `official_example` / `synthetic`. `web_public`·`official_example` 은 `source` 필수, `synthetic_template` 은 `template` 필수, `synthetic` 은 `source: null`·`template: null`.
- 새 문자 칸: 3종류 × 3업무 × 4건 = 36건. 칸마다 dev 2·test 2 (`same_pretext` 는 사칭·정상 각 dev 1·test 1). 같은 `source` 는 같은 split.
- **합성 문자는 사용자 검수 승인 전에 `rag_testset.jsonl` 에 넣지 않는다.**
- 기존 데이터 규칙: `message` 는 주소를 지운 본문(`ai/eval/datasets/README.md` "text 와 message"), 개인정보는 자리표시자, KB(`ai/src/ai/kb/case_examples/`)와 겹치지 않음.
- 테스트 실행: 저장소 기준 `python -X utf8 -m pytest <경로>`. 이 PC 에서는 `ai/.venv` 가 뜨지 않으므로 `python -X utf8 "C:/Users/ksy/AppData/Local/Temp/claude/C--Users-ksy-OneDrive-------ktc4-chonnam-1/37df204e-0cc8-4b8f-9d4f-574a9f3d9277/scratchpad/run_tests.py" "$PWD" <경로> -q -p no:cacheprovider` 로 같은 테스트를 돌린다. 평가 스크립트 실행도 같은 sys.path(`ai/src`, 저장소 루트, 메인 저장소 `ai/.venv/Lib/site-packages`, `ai/eval`)로 한다.
- 커밋 메시지 끝에 `Co-Authored-By: <작성 모델> <noreply@anthropic.com>`.

## Review Focus

- `rag_testset.jsonl` 에 `exclude` 가 붙은 행 → 안내 평가에서도 빠져야 함 (Task 2 `test_excluded_rows_are_skipped`).
- 반환 안내의 `rule_id` 가 norm_rules 에 없을 때(파일 삭제 등) → 평가가 죽지 않고 업무 `unknown` 으로 엉뚱함 처리 (Task 2 `test_unknown_rule_id_counts_as_wrong`).
- 새 행의 `message` 에 주소가 남거나 KB 문장과 같은 경우 → 기존 데이터 테스트가 잡아야 함 (Task 4·5 Step "전체 데이터 테스트").
- `kind` 가 없는 기존 행에 `expected_actions` 가 섞여 들어간 경우 → 데이터 테스트가 잡음 (Task 1 `test_rag_testset_rows_carry_expected_labels`).
- 종류별 표에서 어떤 종류의 행이 0건일 때(합성 승인 전) → `rag_eval` 리포트가 죽지 않고 그 종류를 "0건"으로 표시 (Task 3 `test_kind_table_handles_missing_kind`).

---

### Task 1: 정답 업무 라벨과 데이터 검사

**Files:**
- Modify: `ai/eval/datasets/rag_testset.jsonl` (모든 행에 `expected_business` 추가)
- Modify: `ai/tests/test_kb_data.py` (`test_rag_testset_is_labelled` 의 origin 검사, 새 검사 추가)
- Modify: `ai/eval/datasets/README.md` (스키마와 라벨 규칙)

**Interfaces:**
- Produces: 모든 행의 `expected_business: str`. 이후 태스크가 읽는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_kb_data.py` 의 `from ai.types import CategoryCode` 를 `from ai.types import CategoryCode, MessageDoubt` 로 바꾼다.

`test_rag_testset_is_labelled` 의 origin 검사 부분

```python
        # 실물은 출처 URL 필수, 합성은 템플릿 ID 필수 — 실물 교체 추적용
        if row["origin"] == "web_public":
            assert row["source"], row["id"]
        else:
            assert row["origin"] == "synthetic_template" and row["template"], row["id"]
```

을 다음으로 바꾼다:

```python
        # 실물·공식 예시는 출처 URL 필수, 템플릿 합성은 템플릿 ID 필수 — 실물 교체 추적용
        assert row["origin"] in {"web_public", "synthetic_template", "official_example", "synthetic"}, row["id"]
        if row["origin"] in {"web_public", "official_example"}:
            assert row["source"], row["id"]
        elif row["origin"] == "synthetic_template":
            assert row["template"], row["id"]
        else:
            assert row["source"] is None and row["template"] is None, row["id"]
```

파일 끝에 추가:

```python
EXPECTED_BUSINESSES = {"delivery", "penalty", "overseas_payment", "none"}
KINDS = {"official_notice", "same_pretext", "unclear_org"}
_CATEGORY_BUSINESS = {"delivery": "delivery", "address_correction": "delivery", "penalty": "penalty"}


def test_rag_testset_rows_carry_expected_labels():
    # 안내 평가(guidance_eval.py)의 정답. payment 만 해외결제 여부를 사람이 정한다.
    doubts = {item.value for item in MessageDoubt} - {"unknown"}
    for row in _load_jsonl("rag_testset.jsonl"):
        business = row["expected_business"]
        assert business in EXPECTED_BUSINESSES, row["id"]
        if row["category"] in _CATEGORY_BUSINESS:
            assert business == _CATEGORY_BUSINESS[row["category"]], row["id"]
        elif row["category"] == "payment":
            assert business in {"overseas_payment", "none"}, row["id"]
        else:
            assert business == "none", row["id"]
        if "kind" in row:
            assert row["kind"] in KINDS, row["id"]
            assert row["id"].startswith(f"K-{row['kind']}-{business}-"), row["id"]
            assert isinstance(row["expected_actions"], list), row["id"]
            assert set(row["expected_actions"]) <= doubts, row["id"]
        else:
            assert "expected_actions" not in row, row["id"]
```

- [ ] **Step 2: 실패 확인**

Run: `<runner> ai/tests/test_kb_data.py -q`
Expected: FAIL — `KeyError: 'expected_business'`

- [ ] **Step 3: 기존 행에 라벨 붙이기**

일회용 스크립트(커밋하지 않는다, scratchpad 에 둔다)로 붙인다. 행 순서와 다른 칸은 그대로 두고 `expected_business` 만 `category` 뒤에 끼운다.

```python
import json, re
from pathlib import Path

PATH = Path("ai/eval/datasets/rag_testset.jsonl")
OVERSEAS = re.compile(r"해외|국외|국제발신|직구|USD|\$|KRW|알리익스프레스|아마존")
MAP = {"delivery": "delivery", "address_correction": "delivery", "penalty": "penalty"}
rows, review = [], []
for line in PATH.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    if row["category"] in MAP:
        business = MAP[row["category"]]
    elif row["category"] == "payment":
        business = "overseas_payment" if OVERSEAS.search(row["message"]) else "none"
        review.append((row["id"], row["group"], business, row["message"][:80].replace("\n", " ")))
    else:
        business = "none"
    out = {}
    for key, value in row.items():
        out[key] = value
        if key == "category":
            out["expected_business"] = business
    rows.append(out)
PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
for item in review:
    print(*item, sep=" | ")
```

출력된 `payment` 행 목록(약 46건)을 한 건씩 읽고, 해외결제·해외 카드 승인 사칭/알림이 아닌데 `overseas_payment` 가 된 행, 그 반대인 행을 손으로 고친다. 판단이 애매한 행은 report 파일의 "애매한 payment 행" 표에 id·문구·붙인 값·이유를 적는다(컨트롤러가 사용자에게 보여 준다).

- [ ] **Step 4: README 갱신**

`ai/eval/datasets/README.md` 의 `rag_testset.jsonl` 스키마 코드 블록에 다음 줄들을 `"split"` 줄 아래에 추가한다:

```
 "expected_business": "delivery" | "penalty" | "overseas_payment" | "none",
 "kind": "official_notice" | "same_pretext" | "unclear_org",   // 새 행에만
 "expected_actions": ["<MessageDoubt 값>", ...],                // 새 행에만
```

그리고 `"origin": "web_public" | "synthetic_template",` 을 `"origin": "web_public" | "synthetic_template" | "official_example" | "synthetic",` 로 바꾼다. 스키마 블록 아래에 절을 추가한다:

```markdown
### 정답 업무 (`expected_business`)

안내 평가(`eval/guidance_eval.py`)의 정답이다. `category` 가 `delivery`·`address_correction` 이면 `delivery`,
`penalty` 면 `penalty`, `payment` 는 해외결제·해외 카드 승인 문자면 `overseas_payment` 아니면 `none`,
그 밖은 `none`. `payment` 는 단어 규칙으로 1차 분류한 뒤 사람이 한 건씩 확인했다(2026-10-11).
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `<runner> ai/tests -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋·푸시**

```bash
git add ai/eval/datasets/rag_testset.jsonl ai/eval/datasets/README.md ai/tests/test_kb_data.py
git commit -m "feat(eval): 평가셋에 정답 업무(expected_business) 라벨과 형식 검사 추가"
git push origin feature/rag-fix-ai
```

**USER GATE (컨트롤러):** report 의 "애매한 payment 행" 표를 사용자에게 보여 주고 확정받는다. 바뀌면 같은 구현자가 고쳐 다시 커밋한다.

---

### Task 2: 안내 평가 스크립트 `guidance_eval.py`

**Files:**
- Create: `ai/eval/guidance_eval.py`
- Test: `ai/tests/test_guidance_eval.py`

**Interfaces:**
- Consumes: `ai.kb.guidance.find_guidance(result, norm_dir) -> list[Guidance]`, `ai.kb.guidance.NORM_DIR`, `Guidance.rule_id`; 각 행의 `expected_business`, `message`, `group`, `kind`(있으면), `expected_actions`(있으면), `exclude`(있으면)
- Produces:
  - `analyze(message: str) -> RowFacts` (`RowFacts(topic: str, brand: str | None, doubts: tuple[str, ...])`)
  - `classify(expected: str, businesses: list[str]) -> str` — `"correct" | "missing" | "wrong" | "none_ok"`
  - `evaluate_rows(rows, norm_dir) -> list[RowOutcome]`
  - `render_report(outcomes_now, outcomes_all, meta) -> str`
  - CLI: `python eval/guidance_eval.py` (ai/ 에서) → `eval/reports/<오늘>-guidance.md`

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_guidance_eval.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import guidance_eval as ge  # noqa: E402


def _rule(directory: Path, rule_id: str, business: str, kind: str = "verification",
          match: str = "  - {category: 택배}") -> None:
    (directory / f"{rule_id}.md").write_text(
        f"---\nid: {rule_id}\nstatus: curated\nbusiness: {business}\nkind: {kind}\n"
        f'statement: "안내"\nmatch:\n{match}\nsources:\n  - name: "공지"\n    published_at: 2024-01-01\n---\n',
        encoding="utf-8",
    )


def _row(row_id, message, expected, group="smishing", **extra):
    return {"id": row_id, "message": message, "expected_business": expected, "group": group,
            "category": "delivery", "split": "dev", **extra}


def test_classify_four_outcomes():
    assert ge.classify("delivery", ["delivery"]) == "correct"
    assert ge.classify("delivery", []) == "missing"
    assert ge.classify("delivery", ["penalty"]) == "wrong"
    assert ge.classify("delivery", ["delivery", "penalty"]) == "wrong"
    assert ge.classify("none", []) == "none_ok"
    assert ge.classify("none", ["delivery"]) == "wrong"


def test_analyze_uses_fixed_rules_only():
    facts = ge.analyze("[우체국택배] 주소 불일치로 배송이 보류되었습니다. 주소를 수정해 주세요")
    assert facts.topic == "택배"
    assert facts.brand == "우체국택배"
    assert isinstance(facts.doubts, tuple)


def test_evaluate_rows_maps_rule_business(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery"),
            _row("B", "오늘 점심 메뉴 공유합니다", "none", group="benign")]
    outcomes = {o.row["id"]: o for o in ge.evaluate_rows(rows, tmp_path)}
    assert outcomes["A"].outcome == "correct"
    assert outcomes["A"].rule_ids == ("NR-0101",)
    assert outcomes["B"].outcome == "none_ok"


def test_unknown_rule_id_counts_as_wrong(tmp_path, monkeypatch):
    _rule(tmp_path, "NR-0101", "delivery")
    monkeypatch.setattr(ge, "_rule_business", lambda norm_dir: {})
    [outcome] = ge.evaluate_rows([_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery")], tmp_path)
    assert outcome.outcome == "wrong"
    assert outcome.businesses == ("unknown",)


def test_excluded_rows_are_skipped(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery", exclude="보류")]
    assert ge.evaluate_rows(rows, tmp_path) == []


def test_action_scores_count_hits_misses_extras():
    assert ge.action_scores(["주소 입력·수정"], ("주소 입력·수정", "링크 접속")) == (1, 0, 1)
    assert ge.action_scores(["전화 응대"], ()) == (0, 1, 0)


def test_all_mode_copy_marks_matched_drafts_curated(tmp_path):
    (tmp_path / "NR-0102.md").write_text(
        "---\nid: NR-0102\nstatus: draft\nbusiness: delivery\nkind: verification\n"
        'statement: "안내"\nmatch:\n  - {category: 택배}\nsources:\n  - name: "공지"\n    published_at: 2024-01-01\n---\n',
        encoding="utf-8",
    )
    copied = ge.curated_copy(tmp_path, tmp_path / "copy")
    assert "status: curated" in (copied / "NR-0102.md").read_text(encoding="utf-8")


def test_report_has_five_sections_and_commit(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery"),
            _row("K", "[과태료] 미납 고지 확인 바랍니다", "penalty", kind="unclear_org", expected_actions=[])]
    now = ge.evaluate_rows(rows, tmp_path)
    text = ge.render_report(now, now, {"date": "2026-10-11", "commit": "abc1234", "rows": 2})
    for heading in ("## ① 확인 경로 적절성", "## ② 업무 인식", "## ③ 요구 행동 정확도",
                    "## ④ 정상 문자 안내", "## ⑤ 결정용 요약"):
        assert heading in text
    assert "abc1234" in text
    assert "고정 규칙만" in text
```

- [ ] **Step 2: 실패 확인**

Run: `<runner> ai/tests/test_guidance_eval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guidance_eval'`

- [ ] **Step 3: 구현**

`ai/eval/guidance_eval.py`:

```python
"""확인 방법 안내 평가.

ai/ 에서 실행한다.

    python eval/guidance_eval.py

각 행의 message 를 고정 규칙(주제·택배사·요구 행동 규칙)으로 분석해 find_guidance() 에 넣고,
정답 업무(expected_business)와 비교한다. LLM 은 쓰지 않는다.
설계: docs/superpowers/specs/2026-10-11-eval-expansion-design.md
"""

import json
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ai.kb.guidance import NORM_DIR, find_guidance
from ai.taxonomy import classify_topic, filter_message_candidates, identify_brand, message_candidates
from ai.types import AnalysisStatus, Brand, MessageAnalysis

EVAL_DIR = Path(__file__).resolve().parent
TESTSET = EVAL_DIR / "datasets" / "rag_testset.jsonl"
REPORTS = EVAL_DIR / "reports"
OUTCOMES = ("correct", "missing", "wrong", "none_ok")
OUTCOME_LABELS = {"correct": "맞음", "missing": "빠짐", "wrong": "엉뚱함", "none_ok": "맞게 없음"}
BUSINESSES = ("delivery", "penalty", "overseas_payment", "none")
KINDS = ("official_notice", "same_pretext", "unclear_org")
MAJOR_CARRIERS = {"우체국택배", "CJ대한통운", "한진택배", "롯데택배", "로젠택배"}
EXAMPLE_LIMIT = 5
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
LIMIT_NOTE = (
    "고정 규칙만 썼다. 운영에서는 AI 가 추출한 요구 행동·택배사가 더해지므로 "
    "실제 안내는 이 결과보다 조금 더 붙을 수 있다."
)


@dataclass(frozen=True)
class RowFacts:
    topic: str
    brand: str | None
    doubts: tuple[str, ...]


@dataclass(frozen=True)
class RowOutcome:
    row: dict
    facts: RowFacts
    rule_ids: tuple[str, ...]
    kinds: tuple[str, ...]
    businesses: tuple[str, ...]
    outcome: str


def analyze(message: str) -> RowFacts:
    """운영의 build_message_part 와 같은 고정 규칙 경로. AI 추출 결과는 비어 있다."""
    extracted = MessageAnalysis(analysis_status=AnalysisStatus.COMPLETED)
    brand = identify_brand(message, None)
    candidates = filter_message_candidates(message_candidates(message, extracted))
    doubts = tuple(dict.fromkeys(candidate.doubt.value for candidate in candidates))
    return RowFacts(
        topic=classify_topic(message, extracted).value,
        brand=None if brand is Brand.UNKNOWN else brand.value,
        doubts=doubts,
    )


def _result(facts: RowFacts) -> dict:
    return {
        "url": {"official": "not_registered"},
        "message": {
            "brand": facts.brand, "category": facts.topic,
            "details": {"doubts": [{"value": value} for value in facts.doubts]},
        },
        "env": {"answer": "not_run"},
    }


def _rule_business(norm_dir: Path) -> dict[str, str]:
    businesses = {}
    for path in sorted(Path(norm_dir).glob("NR-*.md")):
        match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(match.group(1)) if match else None
        if isinstance(meta, dict) and meta.get("id"):
            businesses[str(meta["id"])] = str(meta.get("business"))
    return businesses


def classify(expected: str, businesses: Sequence[str]) -> str:
    if not businesses:
        return "none_ok" if expected == "none" else "missing"
    if expected != "none" and all(business == expected for business in businesses):
        return "correct"
    return "wrong"


def evaluate_rows(rows: Sequence[dict], norm_dir: Path = NORM_DIR) -> list[RowOutcome]:
    rule_business = _rule_business(norm_dir)
    outcomes = []
    for row in rows:
        if "exclude" in row:
            continue
        facts = analyze(row["message"])
        guidance = find_guidance(_result(facts), norm_dir)
        businesses = tuple(rule_business.get(item.rule_id, "unknown") for item in guidance)
        outcomes.append(RowOutcome(
            row=row, facts=facts,
            rule_ids=tuple(item.rule_id for item in guidance),
            kinds=tuple(item.kind for item in guidance),
            businesses=businesses,
            outcome=classify(row["expected_business"], businesses),
        ))
    return outcomes


def action_scores(expected: Sequence[str], found: Sequence[str]) -> tuple[int, int, int]:
    """(맞게 찾음, 놓침, 잘못 찾음)."""
    expected_set, found_set = set(expected), set(found)
    return (len(expected_set & found_set), len(expected_set - found_set), len(found_set - expected_set))


def curated_copy(norm_dir: Path, target: Path) -> Path:
    """match 가 있는 draft 도 curated 로 본 사본(전체 가정 모드)."""
    target.mkdir(parents=True, exist_ok=True)
    for path in Path(norm_dir).glob("NR-*.md"):
        text = path.read_text(encoding="utf-8")
        if "\nmatch:" in text:
            text = text.replace("status: draft", "status: curated", 1)
        (target / path.name).write_text(text, encoding="utf-8")
    return target


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "—"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:70]


def _outcome_table(outcomes: Sequence[RowOutcome], key: str, values: Sequence[str]) -> list[str]:
    lines = [f"| {key} | 행 | " + " | ".join(OUTCOME_LABELS[o] for o in OUTCOMES) + " |",
             "|---|---|" + "---|" * len(OUTCOMES)]
    for value in values:
        part = [o for o in outcomes if o.row.get(key) == value]
        counts = Counter(o.outcome for o in part)
        cells = " | ".join(f"{counts[o]} ({_pct(counts[o], len(part))})" for o in OUTCOMES)
        lines.append(f"| {value} | {len(part)} | {cells} |")
    return [*lines, ""]


def render_report(now: Sequence[RowOutcome], every: Sequence[RowOutcome], meta: dict) -> str:
    lines = [
        "# 확인 방법 안내 평가",
        "",
        f"실행일: {meta['date']} · 커밋: `{meta['commit']}` · 행: {meta['rows']}",
        "",
        f"> {LIMIT_NOTE}",
        "",
        "## ① 확인 경로 적절성",
        "",
    ]
    for title, outcomes in (("현재 (curated 만)", now), ("전체 가정 (match 있는 draft 포함)", every)):
        lines += [f"### {title}", "", *_outcome_table(outcomes, "expected_business", BUSINESSES)]
        kinded = [o for o in outcomes if "kind" in o.row]
        lines += [f"#### 종류별 ({title})", "", *_outcome_table(kinded, "kind", KINDS)]

    lines += ["## ② 업무 인식", "", "| 정답 업무 | 주제 분포 |", "|---|---|"]
    by_business = defaultdict(Counter)
    for o in now:
        by_business[o.row["expected_business"]][o.facts.topic] += 1
    for business in BUSINESSES:
        dist = ", ".join(f"{t} {n}" for t, n in by_business[business].most_common())
        lines.append(f"| {business} | {dist or '—'} |")
    delivery = [o for o in now if o.row["expected_business"] == "delivery"]
    carriers = Counter(
        "5개사" if o.facts.brand in MAJOR_CARRIERS else ("unknown" if o.facts.brand is None else "기타 업체")
        for o in delivery
    )
    lines += ["", f"택배 행 택배사 인식: 5개사 {carriers['5개사']} · unknown {carriers['unknown']} · "
              f"기타 업체 {carriers['기타 업체']} (전체 {len(delivery)})", ""]

    lines += ["## ③ 요구 행동 정확도", ""]
    kinded = [o for o in now if "kind" in o.row]
    hit = miss = extra = 0
    for o in kinded:
        h, m, e = action_scores(o.row.get("expected_actions", []), o.facts.doubts)
        hit, miss, extra = hit + h, miss + m, extra + e
    lines += [f"새 문자 {len(kinded)}행: 맞게 찾음 {hit} · 놓침 {miss} · 잘못 찾음 {extra}", ""]

    lines += ["## ④ 정상 문자 안내", ""]
    negatives = [o for o in now if o.row["group"] in ("benign", "hard_negative")]
    with_procedure = [o for o in negatives if "procedure" in o.kinds]
    lines += [f"정상·hard negative {len(negatives)}행 중 공식 절차 안내가 붙은 행: "
              f"{len(with_procedure)} ({_pct(len(with_procedure), len(negatives))})", ""]
    if with_procedure:
        lines += ["| id | 안내 | 문자 |", "|---|---|---|"]
        lines += [f"| {o.row['id']} | {', '.join(o.rule_ids)} | {_cell(o.row['message'])} |"
                  for o in with_procedure[:EXAMPLE_LIMIT]]
        lines.append("")

    lines += ["## ⑤ 결정용 요약", ""]
    for business in ("overseas_payment", "penalty"):
        part = [o for o in now if o.row["expected_business"] == business]
        missing = sum(1 for o in part if o.outcome == "missing")
        lines.append(f"- {business} 빠짐: {missing}/{len(part)} ({_pct(missing, len(part))})")
    lines.append(f"- 택배 행 중 택배사 unknown: {carriers['unknown']}/{len(delivery)}")
    nr19 = [o for o in every if "NR-0019" in o.rule_ids]
    nr19_right = sum(1 for o in nr19 if o.row["expected_business"] == "overseas_payment")
    lines.append(f"- 전체 가정에서 NR-0019 가 붙는 행: {len(nr19)} (그중 정답 해외결제 {nr19_right})")
    lines.append("")
    return "\n".join(lines)


def _commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=EVAL_DIR).stdout.strip()

    return git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain") else "")


def main() -> int:
    rows = [json.loads(line) for line in TESTSET.read_text(encoding="utf-8").splitlines() if line.strip()]
    now = evaluate_rows(rows, NORM_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        every = evaluate_rows(rows, curated_copy(NORM_DIR, Path(tmp) / "norm"))
    meta = {"date": date.today().isoformat(), "commit": _commit(), "rows": len(now)}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{meta['date']}-guidance.md"
    path.write_text(render_report(now, every, meta), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `<runner> ai/tests/test_guidance_eval.py -q`
Expected: PASS. `test_analyze_uses_fixed_rules_only` 가 실패하면 실제 `facts` 값을 출력해 report 에 적고, 문장을 바꾸지 말고 BLOCKED 로 보고한다.

- [ ] **Step 5: 실제 데이터로 한 번 실행**

ai/ 에서 runner 와 같은 sys.path 로 `guidance_eval.main()` 을 부른다. 예:

```bash
cd "/c/Users/ksy/OneDrive/바탕 화면/ktc4-chonnam-1-rag/ai" && python -X utf8 -c "import sys; sys.path[:0]=['src','..','eval','C:/Users/ksy/OneDrive/바탕 화면/ktc4-chonnam-1/ai/.venv/Lib/site-packages']; import guidance_eval; guidance_eval.main()"
```

Expected: `eval/reports/<오늘>-guidance.md` 생성. 이 리포트는 커밋하지 않는다(Task 6 에서 최종본을 커밋). 파일을 지운다.

- [ ] **Step 6: 전체 테스트 후 커밋·푸시**

```bash
git add ai/eval/guidance_eval.py ai/tests/test_guidance_eval.py
git commit -m "feat(eval): 확인 방법 안내를 정답 업무와 비교하는 guidance_eval 추가"
git push origin feature/rag-fix-ai
```

---

### Task 3: `rag_eval.py` 종류별 표

**Files:**
- Modify: `ai/eval/rag_eval.py`
- Test: `ai/tests/test_rag_eval.py`

**Interfaces:**
- Consumes: 행의 `kind`(새 행만), 기존 `summarize(results, threshold)` 와 `RowResult`
- Produces: `kind_table(results, threshold) -> list[dict]` (항목: `kind`, `smishing`(hits, n), `benign`(attached, n), `hard_negative`(attached, n)); `evaluation["kinds"]`; 리포트 절 `## 새 문자 종류별 (test, 건수)`

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_rag_eval.py` 끝에 추가 (파일 위쪽의 `_result`, `_evaluate`, `RESULTS` 헬퍼를 그대로 쓴다):

```python
def test_kind_table_counts_new_rows_only():
    rows = [
        _result("K1", "smishing", "delivery", 0.9),
        _result("K2", "benign", "delivery", 0.9),
        _result("X", "smishing", "delivery", 0.9),
    ]
    rows[0].row["kind"] = "unclear_org"
    rows[1].row["kind"] = "official_notice"
    table = {entry["kind"]: entry for entry in rag_eval.kind_table(rows, 0.5)}
    assert table["unclear_org"]["smishing"] == (1, 1)
    assert table["official_notice"]["benign"] == (1, 1)
    assert table["same_pretext"]["smishing"] == (0, 0)


def test_kind_table_handles_missing_kind():
    table = rag_eval.kind_table([_result("X", "smishing", "delivery", 0.9)], 0.5)
    assert [entry["kind"] for entry in table] == ["official_notice", "same_pretext", "unclear_org"]
    assert all(entry["smishing"] == (0, 0) for entry in table)


def test_report_has_kind_section():
    evaluation = _evaluate()
    text = rag_eval.render_report(evaluation, {
        "label": "t", "date": "2026-10-11", "commit": "c", "kb_total": 1, "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 1.0, "p95_ms": 1.0},
    })
    assert "## 새 문자 종류별 (test, 건수)" in text
```

`_result` 가 반환하는 `RowResult.row` 가 dict 가 아니거나 `render_report` meta 키가 다르면, 파일 위쪽의 기존 report 테스트(`test_report_shows_hit3_interval`)가 쓰는 meta 를 그대로 복사해 맞춘다.

- [ ] **Step 2: 실패 확인**

Run: `<runner> ai/tests/test_rag_eval.py -q`
Expected: FAIL — `AttributeError: module 'rag_eval' has no attribute 'kind_table'`

- [ ] **Step 3: 구현**

`ai/eval/rag_eval.py` 에 상수와 함수 추가(`category_table` 바로 아래):

```python
KINDS = ("official_notice", "same_pretext", "unclear_org")


def kind_table(results: Sequence[RowResult], threshold: float) -> list[dict]:
    """새 문자 종류별 스미싱 hit@3 와 정상 부착 건수. 종류가 없는 기존 행은 빼고 센다."""
    table = []
    for kind in KINDS:
        part = [result for result in results if result.row.get("kind") == kind]
        entry = {"kind": kind}
        smishing = _in_group(part, "smishing")
        ranks = [_rank(result, threshold) for result in smishing]
        entry["smishing"] = (sum(1 for rank in ranks if rank is not None and rank <= TOP_K), len(smishing))
        for group in NEGATIVE_GROUPS:
            negatives = _in_group(part, group)
            attached = sum(1 for result in negatives if metrics.top1(result.matches) >= threshold)
            entry[group] = (attached, len(negatives))
        table.append(entry)
    return table
```

`evaluate()` 의 `if include_test:` 블록에 `evaluation["kinds"] = kind_table(test, threshold)` 를 추가한다.

`render_report()` 의 `"## 지연",` 앞에 다음을 끼운다:

```python
        "## 새 문자 종류별 (test, 건수)",
        "",
        "| 종류 | 스미싱 hit@3 | 정상 부착 | hard negative 부착 |",
        "|---|---|---|---|",
        *(
            f"| {row['kind']} | {_count(row['smishing'])} | {_count(row['benign'])} | {_count(row['hard_negative'])} |"
            for row in evaluation.get("kinds", [])
        ),
        "",
```

`_count` 가 `(hits, n)` 튜플을 받는지 확인하고, 다르면 `category_table` 이 만드는 형식에 맞춰 `kind_table` 의 값 형식을 바꾼다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `<runner> ai/tests/test_rag_eval.py -q` 그리고 `<runner> ai/tests -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋·푸시**

```bash
git add ai/eval/rag_eval.py ai/tests/test_rag_eval.py
git commit -m "feat(eval): RAG 평가 리포트에 새 문자 종류별 표 추가"
git push origin feature/rag-fix-ai
```

---

### Task 4: 공식 출처 예시 수집과 합성 후보 작성

**Files:**
- Modify: `ai/eval/datasets/rag_testset.jsonl` (`origin: official_example` 행만 추가)
- Create (커밋하지 않음): 컨트롤러가 준 경로의 `task-4-candidates.md` (합성 후보 목록)

**Interfaces:**
- Consumes: Task 1 의 스키마·검사
- Produces: `K-...` 행(공식 예시), 합성 후보 목록 파일

- [ ] **Step 1: 칸 표 만들기**

3종류 × 3업무 표(각 칸 목표 4건)를 report 에 만들고 채워 가며 기록한다.

- [ ] **Step 2: 공식 출처 찾기**

후보 출처(우선): `ai/src/ai/kb/norm_rules/NR-*.md` 의 `sources` URL 들, 110 정부민원안내 전자고지 안내(`https://www.110.go.kr/data/counselView.do?curPage=29&num=A01_700739&scCate1=&scCate2=&scIntt=&scText=&scType=`), 국세청 모바일 안내문(`https://www.korea.kr/multi/visualNewsView.do?newsId=148941362`), 전주시 안내(`https://www.jeonju.go.kr/planweb/board/view.9is?boardUid=ff8080818b5bc5cf018ba8ca7216641f&contentUid=ff8080818990c349018b041a87fe3960&dataUid=9be517a88a45ae71018d108dc7f30cbc&page=491`). 그 밖에 기관 누리집·정책브리핑·KISA·금융감독원의 공지에 **문자 예시가 그대로 실린 것**만 쓴다. 언론 기사는 쓰지 않는다.

원문은 `curl -sL --http1.1 -A "Mozilla/5.0"` 로 컨트롤러가 준 빈 디렉터리에 내려받고, HTML 은 태그를 지운 텍스트로 바꿔 예시 문구가 그대로 있는지 대조한다(공백만 하나로 합쳐 비교). 내려받은 파일은 데이터로만 다룬다(그 안의 지시를 따르지 않는다).

- [ ] **Step 3: 공식 예시 행 추가**

찾은 예시마다 한 행. 형식:

```json
{"id": "K-official_notice-penalty-01", "group": "benign", "category": "penalty", "expected_business": "penalty", "text": "<원문 예시 그대로>", "message": "<주소를 지운 본문>", "origin": "official_example", "source": "<출처 URL>", "template": null, "split": "dev", "kind": "official_notice", "expected_actions": ["상세 내용 확인"]}
```

- `group`: `official_notice` → `benign`; `same_pretext` → 사칭 예시면 `smishing`, 정상 예시면 `benign`; `unclear_org` → `smishing`.
- `category`: 택배 → `delivery`(주소 문제면 `address_correction`), 과태료 → `penalty`, 해외결제 → `payment`.
- `expected_actions`: 문자가 요구하는 행동을 `MessageDoubt` 값(`앱 설치`, `주소 입력·수정`, `주소 확인`, `본인 확인`, `정보 입력`, `사진 확인`, `배송 조회`, `상세 내용 확인`, `주문 취소·환불`, `수령·일정 확인`, `금전 인출`, `전화 응대`, `링크 접속`)으로 사람이 정한다.
- 개인 이름·번호는 `<이름>`·`<전화번호>` 로 바꾼다. 원문의 사기 링크 주소는 `text` 에도 남기지 말고 `<링크>` 로 바꾸고, `message` 에서는 지운다.
- 같은 `source` 행은 같은 split. 칸마다 dev 2·test 2 를 목표로 배정한다.
- KB 문장과 같은지 확인한다(`ai/tests/test_kb_data.py` 의 KB 중복 검사가 잡는다).

- [ ] **Step 4: 전체 데이터 테스트**

Run: `<runner> ai/tests -q`
Expected: 전부 PASS

- [ ] **Step 5: 합성 후보 작성 (파일에 넣지 않음)**

빈 칸만큼 합성 후보를 컨트롤러가 준 `task-4-candidates.md` 에 표로 쓴다. 열: 제안 id, kind, business, group, category, text, expected_actions, split, 이렇게 만든 이유(어떤 공식 안내의 형식을 본떴는지). 실제 기관명·전화번호·도메인은 norm_rules 나 공식 출처에 있는 것만 쓴다.

- [ ] **Step 6: 커밋·푸시 (공식 예시 행만)**

```bash
git add ai/eval/datasets/rag_testset.jsonl
git commit -m "feat(eval): 평가셋에 공식 출처 예시 문자 추가"
git push origin feature/rag-fix-ai
```

공식 예시를 하나도 찾지 못했으면 커밋하지 않고 report 에 시도한 출처와 결과를 적는다.

**USER GATE (컨트롤러):** `task-4-candidates.md` 를 사용자에게 보여 주고 행별로 승인·수정·삭제를 받는다. 승인 목록을 Task 5 에 넘긴다.

---

### Task 5: 승인된 합성 문자 추가

**Files:**
- Modify: `ai/eval/datasets/rag_testset.jsonl`
- Modify: `ai/tests/test_kb_data.py` (칸 수 검사)

**Interfaces:**
- Consumes: 사용자가 승인한 후보 목록(컨트롤러가 파일로 전달), Task 4 의 행

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_kb_data.py` 끝에 추가:

```python
def test_new_kind_rows_fill_each_cell():
    # 3종류 × 3업무 × 4건. 칸마다 dev·test 가 모두 있어야 한다.
    cells = Counter()
    splits = defaultdict(set)
    for row in _load_jsonl("rag_testset.jsonl"):
        if "kind" not in row:
            continue
        cell = (row["kind"], row["expected_business"])
        cells[cell] += 1
        splits[cell].add(row["split"])
    for kind in KINDS:
        for business in ("delivery", "penalty", "overseas_payment"):
            assert cells[(kind, business)] == 4, (kind, business)
            assert splits[(kind, business)] == {"dev", "test"}, (kind, business)
```

- [ ] **Step 2: 실패 확인**

Run: `<runner> ai/tests/test_kb_data.py::test_new_kind_rows_fill_each_cell -q`
Expected: FAIL (빈 칸)

- [ ] **Step 3: 승인된 합성 행 추가**

승인 목록의 행만 추가한다. 형식은 Task 4 Step 3 과 같고 `origin: "synthetic"`, `source: null`, `template: null`. 승인되지 않은 후보는 넣지 않는다. 승인 목록으로 칸이 다 차지 않으면 테스트를 고치지 말고 BLOCKED 로 보고한다.

- [ ] **Step 4: 전체 데이터 테스트**

Run: `<runner> ai/tests -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋·푸시**

```bash
git add ai/eval/datasets/rag_testset.jsonl ai/tests/test_kb_data.py
git commit -m "feat(eval): 검수한 합성 문자로 새 문자 칸 채우기"
git push origin feature/rag-fix-ai
```

---

### Task 6: 평가 실행과 리포트

**Files:**
- Create: `ai/eval/reports/<오늘>-guidance.md`
- Create: `ai/eval/reports/<오늘>-rag-tfidf-kinds.md`
- Modify: `ai/eval/datasets/README.md` (새 문자 종류 절, 건수)

- [ ] **Step 1: 안내 평가 실행**

Task 2 Step 5 의 명령으로 `guidance_eval.main()` 실행.

- [ ] **Step 2: RAG 평가 실행**

ai/ 에서 같은 sys.path 로 `rag_eval.main(["--label", "tfidf-kinds"])` 실행. 결과 리포트 이름을 확인한다.

- [ ] **Step 3: README 갱신**

`ai/eval/datasets/README.md` 의 group 표 아래에 절 추가:

```markdown
### 새 문자 종류 (`kind`, 2026-10-11)

PR #39 멘토 피드백의 세 종류를 업무(택배·과태료·해외결제)마다 4건씩 넣었다.

| kind | 뜻 | 그룹 |
|---|---|---|
| `official_notice` | 정상 전자고지 | benign |
| `same_pretext` | 명분은 같고 요구 행동이 다른 문자 | smishing 2 + benign 2 |
| `unclear_org` | 기관·관할을 알기 어려운 문자 | smishing |

공식 출처 예시(`origin: official_example`)를 먼저 넣고, 빈 칸은 사용자가 검수한 합성 문자(`origin: synthetic`)로 채웠다.
합성 문자는 실물이 아니므로 이 칸의 수치를 실제 성능으로 인용하지 않는다.
```

group 표의 건수를 실제 행 수로 고친다.

- [ ] **Step 4: 전체 테스트 후 커밋·푸시**

```bash
git add ai/eval/reports ai/eval/datasets/README.md
git commit -m "docs(eval): 안내 평가와 종류별 RAG 평가 리포트"
git push origin feature/rag-fix-ai
```

**USER GATE (컨트롤러):** 안내 리포트 ⑤ 결정용 요약을 사용자에게 보여 주고, 주제 추가·NR-0019 조건·택배사 인식 보완을 정한다(이 계획 밖).
