# 업무별 공식 안내(norm_rules) 운영 연결 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최종 분석 결과의 정해진 값으로 검토를 마친 norm_rules 의 확인 경로·공식 절차를 고르는 `find_guidance()` 를 만들고, 자료 6건에 적용 조건(`match`)을 단다.

**Architecture:** `ai/src/ai/kb/guidance.py` 가 `norm_rules/*.md` 중 `status: curated` 이고 `match` 가 있는 자료를 읽어 캐시한다. `find_guidance(result)` 는 `AnalysisResponse`(또는 그 dict)에서 `brand`·`category`·`doubts`·`official` 만 꺼내 `match` 와 대조한다. LLM·네트워크·자유 텍스트를 쓰지 않고, 예외를 던지지 않는다. BE 가 이 함수를 부르는 일과 "자세히 보기" 화면은 이슈로 넘긴다.

**Tech Stack:** Python 3.12, PyYAML, pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-10-11-norm-guidance-design.md`

## Global Constraints

- 작업 위치: worktree `C:\Users\ksy\OneDrive\바탕 화면\ktc4-chonnam-1-rag`, 브랜치 `feature/rag-fix-ai`. 커밋 후 푸시한다. PR 은 만들지 않는다(RAG 작업이 모두 끝난 뒤 한 번에).
- 판정에 쓰지 않는다. `result`·`answer`·카드 선택 코드(`ai/verdict.py`, `services/result_card_selector.py`)를 건드리지 않는다.
- `AnalysisResponse` 스키마(`ai/src/ai/types.py`, `docs/scheme.md`)를 바꾸지 않는다. `Topic` 값을 추가하지 않는다.
- `ai/` 안에서 `server`·`services`·`main` 을 import 하지 않는다 (CLAUDE.md 원칙 4).
- 정식(`status: curated`) 자료만 돌려준다. `kind: impersonation` 은 돌려주지 않는다.
- 최대 3개. 순서는 `verification` → `procedure`, 같은 종류 안에서 `rule_id` 순.
- `match` 키는 `category`·`brand`·`doubts`·`official` 만. 값은 해당 enum 의 값(`Topic`, `Brand`, `MessageDoubt`, `DomainMatch`)이고 `unknown` 은 쓰지 않는다.
- 자료 파일의 `status` 는 이 계획에서 바꾸지 않는다. 검토(사용자 승인) 후 별도로 바꾼다.
- 테스트 실행: 저장소 기준 명령은 `python -X utf8 -m pytest <경로> -v`. 이 PC 에서는 `ai/.venv` 가 한글 경로 때문에 뜨지 않으므로 scratchpad 러너 `python -X utf8 <scratchpad>/run_tests.py "$PWD" <경로> -v -p no:cacheprovider` 로 같은 테스트를 돌린다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- BE 가 저장해 둔 예전 결과 dict 에 `message`·`details`·`env` 가 없거나 `None` 인 경우 → 예외 없이 `[]` (Task 1 `test_missing_parts_return_empty`).
- pydantic `AnalysisResponse` 객체를 그대로 넘긴 경우 → dict 와 같은 결과 (Task 1 `test_accepts_pydantic_model`).
- 자료 파일 하나가 깨진 경우(YAML 오류, `match` 가 목록이 아님, 모르는 키) → 그 파일만 건너뛰고 나머지는 동작 (Task 1 `test_broken_rule_file_is_skipped`).
- `message` 와 `env` 가 같은 택배사를 가리키는 경우 → 같은 자료가 두 번 나오지 않음 (Task 1 `test_same_rule_not_duplicated_across_parts`).
- 자료에 `match` 를 enum 이름(`EPOST`)이나 `unknown` 으로 잘못 적은 경우 → 데이터 테스트가 잡음 (Task 2 `test_norm_match_uses_known_keys_and_enum_values`).

---

### Task 1: `find_guidance()` 함수

**Files:**
- Create: `ai/src/ai/kb/guidance.py`
- Test: `ai/tests/test_guidance.py`

**Interfaces:**
- Consumes: `ai.types.AnalysisResponse` (pydantic), norm_rules 파일 형식(`ai/src/ai/kb/norm_rules/template.md`)
- Produces:
  - `NORM_DIR: Path` — `ai/src/ai/kb/norm_rules`
  - `MATCH_KEYS: frozenset[str]` — `{"category", "brand", "doubts", "official"}`
  - `MAX_GUIDANCE: int = 3`
  - `@dataclass(frozen=True) class Guidance: kind: str; text: str; source: str; published_at: str; rule_id: str`
  - `def find_guidance(result: AnalysisResponse | Mapping, norm_dir: Path = NORM_DIR) -> list[Guidance]`

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_guidance.py`:

```python
"""업무별 공식 안내 고르기. 판정과 무관하며 정해진 값만 본다."""

from pathlib import Path

import pytest

from ai.kb.guidance import MAX_GUIDANCE, Guidance, find_guidance
from ai.types import AnalysisResponse


def _write_rule(directory: Path, rule_id: str, *, kind: str = "verification",
                status: str = "curated", match: str | None = "  - {category: 택배}",
                statement: str = "안내 문장") -> None:
    match_block = f"match:\n{match}\n" if match is not None else ""
    (directory / f"{rule_id}.md").write_text(
        "---\n"
        f"id: {rule_id}\n"
        f"status: {status}\n"
        "business: delivery\n"
        f"kind: {kind}\n"
        f'statement: "{statement}"\n'
        f"{match_block}"
        "sources:\n"
        '  - name: "출처 공지"\n'
        "    published_at: 2024-03-06\n"
        "---\n",
        encoding="utf-8",
    )


def _result(*, brand=None, category=None, doubts=(), env_brand=None,
            env_category=None, official="not_registered") -> dict:
    return {
        "url": {"final_url": "https://x.example", "domain": "x.example",
                "official": official, "scan": {"score": None, "scanned_at": None}},
        "message": {
            "brand": brand, "category": category, "answer": "no_risk_found",
            "details": {"doubts": [{"value": d, "evidence": "근거 문장"} for d in doubts],
                        "signals": [], "reason": {"text": "설명", "failures": []}},
        },
        "env": {
            "brand": env_brand, "category": env_category, "answer": "not_run",
            "collected_at": None,
            "details": {"doubts": [], "signals": [],
                        "reason": {"text": "페이지 미확인", "failures": ["missing_result"]}},
        },
        "result": False,
    }


def test_category_match_returns_guidance_fields(tmp_path):
    _write_rule(tmp_path, "NR-0101", statement="택배사 고객센터에 직접 확인하세요.")
    assert find_guidance(_result(category="택배"), tmp_path) == [
        Guidance(kind="verification", text="택배사 고객센터에 직접 확인하세요.",
                 source="출처 공지", published_at="2024-03-06", rule_id="NR-0101"),
    ]


def test_all_keys_in_one_condition_must_match(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {category: 금융, doubts: 전화 응대}")
    assert find_guidance(_result(category="금융"), tmp_path) == []
    assert [g.rule_id for g in find_guidance(
        _result(category="금융", doubts=["전화 응대"]), tmp_path)] == ["NR-0101"]


def test_any_condition_in_list_matches(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 한진택배}\n  - {brand: 롯데택배}")
    assert [g.rule_id for g in find_guidance(_result(brand="롯데택배"), tmp_path)] == ["NR-0101"]


def test_env_values_also_match(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    assert [g.rule_id for g in find_guidance(_result(env_brand="우체국택배"), tmp_path)] == ["NR-0101"]


def test_same_rule_not_duplicated_across_parts(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    hits = find_guidance(_result(brand="우체국택배", env_brand="우체국택배"), tmp_path)
    assert [g.rule_id for g in hits] == ["NR-0101"]


def test_draft_impersonation_and_unmatched_rules_are_excluded(tmp_path):
    _write_rule(tmp_path, "NR-0101", status="draft")
    _write_rule(tmp_path, "NR-0102", kind="impersonation")
    _write_rule(tmp_path, "NR-0103", match=None)
    assert find_guidance(_result(category="택배"), tmp_path) == []


def test_verification_first_then_rule_id_and_at_most_three(tmp_path):
    _write_rule(tmp_path, "NR-0101", kind="procedure")
    _write_rule(tmp_path, "NR-0102", kind="verification")
    _write_rule(tmp_path, "NR-0103", kind="procedure")
    _write_rule(tmp_path, "NR-0104", kind="procedure")
    hits = find_guidance(_result(category="택배"), tmp_path)
    assert len(hits) == MAX_GUIDANCE == 3
    assert [g.rule_id for g in hits] == ["NR-0102", "NR-0101", "NR-0103"]


def test_unknown_business_returns_empty(tmp_path):
    _write_rule(tmp_path, "NR-0101")
    assert find_guidance(_result(category="공공기관"), tmp_path) == []


@pytest.mark.parametrize("result", [
    None, "문자열", 42, {}, {"message": None, "env": None},
    {"message": {"details": None}}, {"message": {"details": {"doubts": "아님"}}},
    {"message": {"brand": 123, "category": ["택배"]}},
])
def test_missing_parts_return_empty(tmp_path, result):
    _write_rule(tmp_path, "NR-0101")
    assert find_guidance(result, tmp_path) == []


def test_missing_norm_dir_returns_empty(tmp_path):
    assert find_guidance(_result(category="택배"), tmp_path / "없음") == []


def test_broken_rule_file_is_skipped(tmp_path):
    (tmp_path / "NR-0100.md").write_text("---\nid: [깨진\n---\n", encoding="utf-8")
    _write_rule(tmp_path, "NR-0101", match="  {category: 택배}")      # 목록이 아님
    _write_rule(tmp_path, "NR-0102", match="  - {sender: 우체국}")   # 모르는 키
    _write_rule(tmp_path, "NR-0103")
    assert [g.rule_id for g in find_guidance(_result(category="택배"), tmp_path)] == ["NR-0103"]


def test_accepts_pydantic_model(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    raw = _result(brand="우체국택배", category="택배")
    model = AnalysisResponse.model_validate(raw)
    assert find_guidance(model, tmp_path) == find_guidance(raw, tmp_path)
    assert [g.rule_id for g in find_guidance(model, tmp_path)] == ["NR-0101"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -X utf8 -m pytest ai/tests/test_guidance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai.kb.guidance'`

- [ ] **Step 3: 구현**

`ai/src/ai/kb/guidance.py`:

```python
"""업무별 공식 안내 고르기.

판정이 아닙니다. 최종 분석 결과의 정해진 값(brand·category·doubts·official)만 보고
검토를 마친(curated) norm_rules 의 확인 경로·공식 절차를 고릅니다. 문자 원문·페이지
같은 자유 텍스트와 LLM 은 쓰지 않습니다. 결과는 result·answer·카드 선택에 쓰지 않습니다.
설계: docs/superpowers/specs/2026-10-11-norm-guidance-design.md
"""

import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)

NORM_DIR = Path(__file__).resolve().parent / "norm_rules"
MATCH_KEYS = frozenset({"category", "brand", "doubts", "official"})
# "자세히 보기" 캐러셀 카드 1장이 128자라 많이 넘겨도 다 보여 줄 수 없다.
MAX_GUIDANCE = 3
_KIND_ORDER = {"verification": 0, "procedure": 1}
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_LOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class Guidance:
    kind: str
    text: str
    source: str
    published_at: str
    rule_id: str


@dataclass(frozen=True)
class _Rule:
    guidance: Guidance
    match: tuple[dict[str, str], ...]


def _parse_rule(path: Path) -> _Rule | None:
    try:
        match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(match.group(1)) if match else None
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning("norm rule skipped: %s", type(exc).__name__)
        return None
    if not isinstance(meta, dict) or meta.get("status") != "curated":
        return None
    kind = meta.get("kind")
    conditions = meta.get("match")
    sources = meta.get("sources")
    text = str(meta.get("statement") or "").strip()
    if (kind not in _KIND_ORDER or not text or not isinstance(conditions, list)
            or not conditions or not isinstance(sources, list) or not sources
            or not isinstance(sources[0], dict)):
        return None
    parsed = []
    for condition in conditions:
        if not isinstance(condition, dict) or not condition or not set(condition) <= MATCH_KEYS:
            return None
        parsed.append({str(key): str(value) for key, value in condition.items()})
    first = sources[0]
    return _Rule(
        guidance=Guidance(
            kind=kind, text=text, source=str(first.get("name") or ""),
            published_at=str(first.get("published_at") or ""),
            rule_id=str(meta.get("id") or path.stem),
        ),
        match=tuple(parsed),
    )


def load_rules(norm_dir: Path = NORM_DIR) -> tuple[_Rule, ...]:
    """search.load_cases 와 같이 프로세스 수명 동안 캐시한다. 자료를 고치면 재기동한다."""
    with _LOAD_LOCK:
        return _load_rules(Path(norm_dir))


@lru_cache(maxsize=8)
def _load_rules(norm_dir: Path) -> tuple[_Rule, ...]:
    if not norm_dir.is_dir():
        return ()
    rules = (_parse_rule(path) for path in sorted(norm_dir.glob("NR-*.md")))
    return tuple(rule for rule in rules if rule is not None)


def _strings(value: Any) -> set[str]:
    return {value} if isinstance(value, str) else set()


def _facts(result: Any) -> dict[str, set[str]] | None:
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    if not isinstance(result, Mapping):
        return None
    facts: dict[str, set[str]] = {key: set() for key in MATCH_KEYS}
    for part_name in ("message", "env"):
        part = result.get(part_name)
        if isinstance(part, Mapping):
            facts["brand"] |= _strings(part.get("brand"))
            facts["category"] |= _strings(part.get("category"))
    message = result.get("message")
    details = message.get("details") if isinstance(message, Mapping) else None
    doubts = details.get("doubts") if isinstance(details, Mapping) else None
    for doubt in doubts if isinstance(doubts, list) else ():
        if isinstance(doubt, Mapping):
            facts["doubts"] |= _strings(doubt.get("value"))
    url = result.get("url")
    if isinstance(url, Mapping):
        facts["official"] |= _strings(url.get("official"))
    return facts


def find_guidance(result: Any, norm_dir: Path = NORM_DIR) -> list[Guidance]:
    """맞는 확인 경로·공식 절차를 최대 3개 돌려준다. 예외를 던지지 않는다."""
    try:
        facts = _facts(result)
        if facts is None:
            return []
        hits = [
            rule.guidance for rule in load_rules(norm_dir)
            if any(all(value in facts[key] for key, value in condition.items())
                   for condition in rule.match)
        ]
    except Exception as exc:  # 설명용 부가 기능이라 실패해도 응답을 막지 않는다 (원칙 3)
        LOGGER.warning("guidance lookup failed: %s", type(exc).__name__)
        return []
    hits.sort(key=lambda item: (_KIND_ORDER[item.kind], item.rule_id))
    return hits[:MAX_GUIDANCE]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -X utf8 -m pytest ai/tests/test_guidance.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add ai/src/ai/kb/guidance.py ai/tests/test_guidance.py
git commit -m "feat(kb): 정해진 값으로 공식 안내를 고르는 find_guidance 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 자료 6건에 `match` 추가, 데이터 검사, 검토 기준 문서화

**Files:**
- Modify: `ai/src/ai/kb/norm_rules/NR-0011.md`, `NR-0012.md`, `NR-0013.md`, `NR-0014.md`, `NR-0015.md`, `NR-0019.md` (frontmatter 에 `match` 추가)
- Modify: `ai/src/ai/kb/norm_rules/template.md` (`match` 칸과 검토 기준)
- Modify: `ai/src/ai/kb/README.md` (규범 활용 범위 문단 갱신)
- Test: `ai/tests/test_kb_data.py` (match 검사 추가), `ai/tests/test_guidance.py` (실제 자료 통합 테스트 추가)

**Interfaces:**
- Consumes: Task 1 의 `find_guidance`, `MATCH_KEYS`, `NORM_DIR`
- Produces: 자료 파일의 `match` 형식 (`match: [ {키: 값, ...}, ... ]`)

- [ ] **Step 1: 실패하는 테스트 작성**

`ai/tests/test_kb_data.py` 끝에 추가:

```python
def test_norm_match_uses_known_keys_and_enum_values():
    # match 는 find_guidance 가 정해진 값과 대조하는 조건이다. enum 이름(EPOST)이나
    # unknown 을 적으면 조용히 아무것도 안 맞으므로 여기서 잡는다.
    from ai.kb.guidance import MATCH_KEYS
    from ai.types import Brand, DomainMatch, MessageDoubt, Topic

    allowed = {
        "category": {item.value for item in Topic} - {"unknown"},
        "brand": {item.value for item in Brand} - {"unknown"},
        "doubts": {item.value for item in MessageDoubt} - {"unknown"},
        "official": {item.value for item in DomainMatch},
    }
    assert set(allowed) == MATCH_KEYS
    matched = set()
    for path, meta in _norm_records():
        if "match" not in meta:
            continue
        matched.add(meta["id"])
        assert meta["kind"] in {"verification", "procedure"}, path.name
        assert isinstance(meta["match"], list) and meta["match"], path.name
        for condition in meta["match"]:
            assert isinstance(condition, dict) and condition, path.name
            for key, value in condition.items():
                assert key in MATCH_KEYS, (path.name, key)
                assert value in allowed[key], (path.name, key, value)
    assert matched == {"NR-0011", "NR-0012", "NR-0013", "NR-0014", "NR-0015", "NR-0019"}
```

`ai/tests/test_guidance.py` 끝에 추가:

```python
from ai.kb.guidance import NORM_DIR


def _curated_copy(tmp_path: Path) -> Path:
    # 검토 전(draft)이라도 match 가 의도대로 작동하는지 보려고 상태만 바꾼 사본을 쓴다.
    for path in NORM_DIR.glob("NR-*.md"):
        text = path.read_text(encoding="utf-8").replace("status: draft", "status: curated", 1)
        (tmp_path / path.name).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("result", "expected"), [
    (_result(category="택배"), ["NR-0015"]),
    (_result(brand="우체국택배", category="택배"), ["NR-0015", "NR-0011"]),
    (_result(brand="한진택배", category="택배"), ["NR-0015", "NR-0012"]),
    (_result(brand="롯데택배", category="택배"), ["NR-0015", "NR-0013"]),
    (_result(brand="로젠택배", category="택배"), ["NR-0015", "NR-0014"]),
    (_result(category="금융", doubts=["전화 응대"]), ["NR-0019"]),
    (_result(category="금융"), []),
    (_result(category="공공기관"), []),
])
def test_real_rules_match_as_designed(tmp_path, result, expected):
    assert [g.rule_id for g in find_guidance(result, _curated_copy(tmp_path))] == expected


def test_real_rules_return_nothing_until_reviewed():
    # 지금 자료는 모두 draft 다. 사용자 검토 후 curated 로 바뀌기 전에는 아무것도 나가지 않는다.
    drafts = all("status: draft" in p.read_text(encoding="utf-8") for p in NORM_DIR.glob("NR-*.md"))
    if not drafts:
        pytest.skip("검토를 마친 자료가 생겼다")
    assert find_guidance(_result(brand="우체국택배", category="택배")) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -X utf8 -m pytest ai/tests/test_kb_data.py::test_norm_match_uses_known_keys_and_enum_values ai/tests/test_guidance.py -v`
Expected: FAIL — `matched == set()` 단언 실패, `test_real_rules_match_as_designed` 가 `[]` 를 받아 실패

- [ ] **Step 3: 자료 6건에 `match` 추가**

각 파일 frontmatter 의 `exceptions:` 바로 위(= `statement:` 다음 줄)에 넣는다.

`NR-0015.md`:
```yaml
match:
  - {category: 택배}
```

`NR-0011.md`:
```yaml
match:
  - {brand: 우체국택배}
```

`NR-0012.md`:
```yaml
match:
  - {brand: 한진택배}
```

`NR-0013.md`:
```yaml
match:
  - {brand: 롯데택배}
```

`NR-0014.md`:
```yaml
match:
  - {brand: 로젠택배}
```

`NR-0019.md`:
```yaml
match:
  - {category: 금융, doubts: 전화 응대}
```

- [ ] **Step 4: 템플릿과 README 갱신**

`ai/src/ai/kb/norm_rules/template.md` frontmatter 의 `pattern:` 블록 바로 앞에 추가:

```yaml
match: []                    # procedure·verification 만. 예: - {category: 택배} / - {brand: 우체국택배}
```

같은 파일 본문 끝에 추가:

```markdown
## 적용 조건 (`match`)

`ai/kb/guidance.py` 의 `find_guidance()` 가 최종 분석 결과의 정해진 값과 대조한다. 판정에는 쓰지 않는다.

- 목록의 항목 중 하나라도 맞으면 적용한다. 한 항목 안의 조건은 모두 맞아야 한다.
- 키: `category`(Topic 값), `brand`(Brand 값), `doubts`(MessageDoubt 값), `official`(DomainMatch 값).
  `category`·`brand` 는 `message`·`env` 중 어느 쪽이 맞아도 된다. `unknown` 은 쓰지 않는다.
- `match` 가 없으면 쓰이지 않는다. `impersonation` 에는 달지 않는다.
- 자료의 적용 범위(`applies_to`)보다 넓게 걸지 않는다. 한 기관의 절차는 그 기관 `brand` 에만 건다.

## 검토 기준 (draft → curated)

검토자가 아래를 모두 확인한 뒤 `status: curated`, `reviewer`, `reviewed_at` 을 채운다.

1. 발췌문(`quote`)이 출처 원문에 그대로 있는가 — 원문을 내려받아 기계로 대조한 결과를 첨부한다.
2. `statement` 가 발췌문이 직접 말하는 범위를 넘지 않는가.
3. 적용 범위(`applies_to`)와 `exceptions` 가 출처와 맞는가.
4. 출처가 아직 열리는가, 더 최신 공지가 있는가.
5. 전화번호가 지금도 맞는가.
6. `match` 가 자료의 적용 범위보다 넓지 않은가.
```

`ai/src/ai/kb/README.md` 의 문단
`norm_rules의 검토 규범은 사례와 구별한다. 검색된 규범이나 사례만으로 실행 판정 규칙을 생성·변경하지 않는다. 구체적인 규범 활용 범위는 판정 규칙 확정에 맞춰 정한다.`
를 다음으로 바꾼다:

```markdown
norm_rules의 검토 규범은 사례와 구별한다. 검색된 규범이나 사례만으로 실행 판정 규칙을 생성·변경하지 않는다.
규범은 사용자에게 업무 확인 방법을 안내하는 데만 쓴다. `guidance.py` 의 `find_guidance()` 가 최종 분석
결과의 정해진 값(brand·category·doubts·official)과 각 규범의 `match` 를 대조해 curated 인 확인 경로·공식
절차를 최대 3개 고른다. 적용 조건과 검토 기준은 `norm_rules/template.md`, 설계는
`docs/superpowers/specs/2026-10-11-norm-guidance-design.md`.
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -X utf8 -m pytest ai/tests -q`
Expected: 전부 PASS (`test_real_rules_return_nothing_until_reviewed` 포함)

- [ ] **Step 6: 커밋**

```bash
git add ai/src/ai/kb/norm_rules ai/src/ai/kb/README.md ai/tests/test_kb_data.py ai/tests/test_guidance.py
git commit -m "feat(kb): 택배·카드사 확인 안내 6건에 적용 조건(match)과 검토 기준 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push origin feature/rag-fix-ai
```

---

### Task 3: BE·FE 이슈 등록

**Files:** 없음 (GitHub 이슈)

**Interfaces:**
- Consumes: Task 1 의 `find_guidance(result) -> list[Guidance]`, `Guidance` 필드
- Produces: 이슈 번호 (메모리 `rag-mentor-roadmap` 에 기록)

- [ ] **Step 1: 기존 이슈와 겹치는지 확인**

Run: `gh issue list --state open --search "자세히 보기" --json number,title`
Expected: "자세히 보기 경로"·"확인 방법 카드"를 다루는 열린 이슈가 없음. 있으면 새로 만들지 않고 그 이슈에 Step 2 본문을 댓글로 단다.

- [ ] **Step 2: 이슈 생성**

~~~bash
gh issue create --label enhancement \
  --title "[FE/BE] 자세히 보기에 업무별 공식 확인 방법 카드 추가" \
  --body-file - <<'EOF'
## 무엇

`feature/rag-fix` 에 `ai.kb.guidance.find_guidance()` 가 추가됩니다
(설계 `docs/superpowers/specs/2026-10-11-norm-guidance-design.md`).
최종 분석 결과를 넣으면 검토를 마친 공식 자료 중 맞는 **확인 경로·공식 절차**를 최대 3개 돌려줍니다.
스키마(`AnalysisResponse`)는 바뀌지 않습니다. 판정·카드 선택에 쓰지 않습니다.

```python
from ai.kb.guidance import find_guidance

items = find_guidance(final_payload)  # dict 또는 AnalysisResponse
# [Guidance(kind="verification", text="...", source="인터넷우체국 공지",
#           published_at="2024-03-06", rule_id="NR-0015"), ...]
```

- 지금 연결된 업무: 택배(택배사 고객센터, 우체국·한진·롯데·로젠 공식 절차), 금융 + 전화 응대(카드사 고객센터).
- 자료가 검토(curated)를 마치기 전에는 항상 `[]` 입니다.
- 예외를 던지지 않습니다. 맞는 게 없으면 `[]` — 카드를 붙이지 않으면 됩니다.

## 완료 조건

**BE**
- [ ] "자세히 보기" 버튼을 누르면 캐러셀을 돌려주는 스킬 경로 (현재 없음. 2026-10-02 에 버튼을 뺀 이유)
- [ ] 최종 결과(`final_payload`)로 `find_guidance()` 를 불러 확인 방법 카드를 채움. 결과가 `[]` 면 카드를 넣지 않음
- [ ] 캐러셀 전체 10장 이하 유지

**FE**
- [ ] 확인 방법 카드 템플릿 (textCard 캐러셀 128자)
  - `verification`: `text` 를 그대로
  - `procedure`: "{source}는 ~라고 안내했어요({published_at 연도})" 형태. "그래서 이 문자는 가짜" 같은 결론을 붙이지 않음
- [ ] R9(판단 보류)·R1(위험) 카드의 "자세히 보기" 버튼과 관리자센터 블록
- [ ] C3 행동 안내 문구가 R1 전용("스미싱이 의심된다는 결과…")이라 R9 에서도 맞게 조정
- [ ] `docs/designs/chatbot-copy.md` 4절에 규칙 기록

**이번에 하지 않을 것**
- 과태료·해외결제 세부 안내 (주제 값이 넓어 평가 후 결정)

## 담당 영역
- [x] backend  - [ ] ai  - [x] frontend  - [x] docs
EOF
~~~

Expected: 이슈 URL 출력

- [ ] **Step 3: 기록**

메모리 `rag-mentor-roadmap.md` 에 이슈 번호와 "norm_rules 운영 연결 AI 쪽 완료, 다음: 사용자 검토로 curated 전환 → 로드맵 4단계(평가셋 확장)"를 적는다.
