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
