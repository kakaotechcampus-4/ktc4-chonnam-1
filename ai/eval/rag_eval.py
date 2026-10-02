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
from ai.kb import search
from ai.kb.search import Case, build_index, load_cases, rank, search_cases
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
    parser.add_argument("--ngram", type=parse_ngram, help="예: 2,3. 지정하면 그 설정으로 색인을 새로 만든다")
    parser.add_argument("--tf", choices=("raw", "log"), help="TF 가중: 원 빈도 또는 1+ln(tf)")
    args = parser.parse_args(argv)

    cases = load_cases()
    ngram_sizes = args.ngram or search.NGRAM_SIZES
    sublinear_tf = search.SUBLINEAR_TF if args.tf is None else args.tf == "log"
    if args.ngram is None and args.tf is None:
        searcher = default_searcher
    else:
        searcher = make_searcher(cases, ngram_sizes, sublinear_tf)
    results = run_search(load_rows(), searcher)
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
        "settings": f"문자 n-gram {ngram_sizes} TF-IDF 코사인, TF {'1+ln(tf)' if sublinear_tf else '원 빈도'}",
        "latency": latency(results),
    }
    path = REPORTS / f"{meta['date']}-rag-{args.label}.md"
    path.write_text(render_report(evaluation, meta), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
