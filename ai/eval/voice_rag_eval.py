"""통화 사례 검색(voice KB) 평가.

ai/ 에서 실행한다. KB 와 평가셋은 voice_rag_build.py 가 만든다.

    python eval/voice_rag_eval.py --dev-only [조정 옵션]   # 설정 조정용. dev 지표 한 줄을 출력한다
    python eval/voice_rag_eval.py --label <방식>           # 정한 설정으로 test 까지 재고 리포트를 쓴다

hit@3 은 보이스피싱 질의의 상위 3개(기준값 이상) 중 같은 type 사례가 있는 비율이다.
기준값은 dev 정상 통화 부착률이 MAX_NORMAL_RATE 이하가 되는 가장 낮은 값이다.
test 는 설정을 정한 뒤 한 번만 본다.
"""

import argparse
import re
import statistics
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

import rag_metrics as metrics
from ai.case.attach_gate import load_model
from ai.case.lexicon import identity_kinds
from ai.case.types import IdentityKind
from ai.case.privacy import mask_pii
from ai.case.voice import IDENTITY_PREFERRED_TYPES, search_gated
from ai.kb import search
from ai.kb.normalize import normalize
from ai.kb.search import Case, build_index, case_paths, load_cases, rank, search_voice_cases
from ai.types import CaseMatch
from voice_rag_build import SEED, load_dataset

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "datasets" / "voice_rag.jsonl"
REPORTS = EVAL_DIR / "reports"
TOP_K = 3
FAILURE_LIMIT = 10
MAX_NORMAL_RATE = 0.05
TARGET_HIT_AT_3 = 0.60
QA_KIND = "qa_written"
SYNTHETIC_WARNING = "정상 통화는 모두 합성 대본이다. 정상 부착률을 실제 오탐률로 인용하지 않는다."
REAL_NORMAL_NOTE = (
    "정상 통화에 실제 대본이 섞여 있다(출처별 건수는 아래 분해). 합성분 부착률만 합성 기준이고, "
    "실제 정상 부착률도 표본 한 곳의 값이라 실제 오탐률로 인용하지 않는다."
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)

Searcher = Callable[[str], list[CaseMatch]]


@dataclass(frozen=True)
class RowResult:
    row: dict
    matches: list[CaseMatch]
    seconds: float


def load_rows(path: Path = DATASET) -> list[dict]:
    # 옆에 *_licensed.jsonl 이 있으면 함께 읽는다. 공개 클론에서는 합성 행만 남는다.
    return load_dataset(path)


def kb_types(cases_dir: Path = search.VOICE_CASES_DIR) -> dict[str, str]:
    # 통화 유형은 CaseMatch.categories(문자 주제)에 없어서 레코드 frontmatter 에서 읽는다.
    types = {}
    for path in case_paths(cases_dir):
        match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        if match is not None:
            meta = yaml.safe_load(match.group(1))
            if meta.get("status") == "curated":
                types[str(meta["id"])] = meta["type"]
    return types


def chunk(text: str, size: int) -> list[str]:
    """어절을 끊지 않고 size 자 남짓의 조각으로 나눈다. 조각은 절반씩 겹친다."""
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
    return pieces


def chunked(cases: Sequence[Case], size: int) -> list[Case]:
    # 조각을 같은 사례의 variant 로 둔다. rank() 가 사례마다 가장 높은 조각 하나만
    # 남기므로 한 통화가 상위 3개를 혼자 차지하지 않는다.
    result = []
    for case in cases:
        pieces = tuple(piece for variant in case.variants for piece in chunk(variant, size))
        result.append(Case(case.case_id, pieces, tuple(normalize(p) for p in pieces), case.categories))
    return result


# 신원 주장 종류별로 앞세울 사례 유형. 신원 어휘표는 기관·지인만 낸다.
IDENTITY_TYPES: dict[IdentityKind, frozenset[str]] = {
    **IDENTITY_PREFERRED_TYPES,  # 운영 대응표
    IdentityKind.INSTITUTION: frozenset({"institution_impersonation", "loan_fraud"}),  # 실험용, 채택 안 함
}
IDENTITY_SETS = {
    "all": tuple(IDENTITY_TYPES),
    "acquaintance": (IdentityKind.ACQUAINTANCE,),
}
FULL_RANK_K = 10_000


def preferred_types(text: str, kinds: Sequence[IdentityKind]) -> frozenset[str]:
    found = identity_kinds(text)
    return frozenset().union(*(IDENTITY_TYPES[k] for k in kinds if k in found))


def prefer_types(
    matches: Sequence[CaseMatch],
    preferred: frozenset[str],
    types: Mapping[str, str],
    threshold: float,
    mode: str,
    boost: float,
) -> list[CaseMatch]:
    """기준값 이상 후보 안에서만 순서를 바꾼다. 붙일지는 원점수 1위로 이미 정해져 있다."""
    if mode == "score":
        # 운영과 같은 함수. 점수 자체에 가점을 줘 붙일지 결정에도 영향을 준다(기준값은 다시 고른다)
        return search.boost_types(matches, types, preferred, boost)
    candidates = [m for m in matches if m.similarity >= threshold]
    if not preferred or not candidates:
        return candidates
    if mode == "filter":
        chosen = [m for m in candidates if types.get(m.case_id) in preferred]
        return chosen or candidates
    return sorted(
        candidates,
        key=lambda m: m.similarity + (boost if types.get(m.case_id) in preferred else 0.0),
        reverse=True,
    )


def apply_identity(
    results: Sequence[RowResult],
    types: Mapping[str, str],
    kinds: Sequence[IdentityKind],
    threshold: float,
    mode: str,
    boost: float,
    query_side: str = "all",
) -> list[RowResult]:
    return [
        RowResult(
            r.row,
            prefer_types(
                r.matches, preferred_types(query_text(r.row, query_side), kinds), types, threshold, mode, boost
            )[:TOP_K],
            r.seconds,
        )
        for r in results
    ]


def default_searcher(text: str) -> list[CaseMatch]:
    """운영 경로(ai.case.voice.search_gated: 신원 가점 -> 기준값 -> 관문)를 기준값 0 으로 부른다.

    기준값은 evaluate() 가 고르므로 여기서는 0 으로 둔다. 같은 점수순이라 운영 기준값을 적용한 결과와
    같은 사례가 앞에 온다(tests/test_voice_rag_eval.py 가 확인한다). 관문이 막으면 빈 목록이다.
    """
    return search_gated(text, top_k=TOP_K, min_similarity=0.0).matches


def similarity_only_searcher(text: str) -> list[CaseMatch]:
    """실험용(--similarity-only): 신원 가점·관문 없이 유사도만. 운영 경로가 아니다."""
    return search_voice_cases(text, min_similarity=0.0, top_k=TOP_K).matches


_RANK_CACHE: dict = {}


def rank_all(text: str, tuned: bool, ngram_sizes: tuple[int, ...], sublinear_tf: bool, chunk_size: int) -> list[CaseMatch]:
    """모든 사례를 점수순으로. --identity 가 기준값 이상 후보 안에서 순서를 바꾼다."""
    if not tuned:
        return search_voice_cases(text, min_similarity=0.0, top_k=FULL_RANK_K).matches
    if "s" not in _RANK_CACHE:
        cases = load_cases(search.VOICE_CASES_DIR)
        cases = chunked(cases, chunk_size) if chunk_size else cases
        _RANK_CACHE["s"] = build_index(cases, ngram_sizes=ngram_sizes, sublinear_tf=sublinear_tf)
    return rank(_RANK_CACHE["s"], text)


def make_searcher(
    cases: Sequence[Case], ngram_sizes: tuple[int, ...], sublinear_tf: bool, chunk_size: int
) -> Searcher:
    if chunk_size:
        cases = chunked(cases, chunk_size)
    index = build_index(cases, ngram_sizes=ngram_sizes, sublinear_tf=sublinear_tf)
    return lambda text: rank(index, text)[:TOP_K]


OTHER_SPEAKER = "상대"


def query_text(row: dict, query_side: str = "all") -> str:
    """질의 글. other 이면 turns 의 상대 발화만 쓴다. turns 가 없거나 상대 발화가 없으면 전체.

    운영처럼 mask_pii 를 거친다(평가셋은 휴대전화 번호만 가려 두었다). 운영은 STT 세그먼트를 줄바꿈으로
    잇고 평가셋 text 는 공백으로 이어져 있어, 문장 나눔이 조금 다를 수 있다.
    """
    if query_side == "other":
        other = [t["text"] for t in row.get("turns") or [] if t.get("speaker") == OTHER_SPEAKER]
        if other:
            return mask_pii("\n".join(other))
    return mask_pii(row["text"])


def apply_gate(results: Sequence[RowResult], gate_min: float, query_side: str = "all") -> list[RowResult]:
    """운영 관문(ai.case.attach_gate)과 같다. 관문 점수가 gate_min 미만인 질의는 사례를 붙이지 않는다."""
    model = load_model()
    return [
        RowResult(r.row, list(r.matches) if model.score(query_text(r.row, query_side)) >= gate_min else [], r.seconds)
        for r in results
    ]


def run_search(
    rows: Sequence[dict], searcher: Searcher, query_head: int = 0, query_side: str = "all"
) -> list[RowResult]:
    results = []
    for row in rows:
        text = query_text(row, query_side)
        text = text[:query_head] if query_head else text
        start = time.perf_counter()
        matches = searcher(text)
        results.append(RowResult(row, matches, time.perf_counter() - start))
    return results


def first_hit_rank(
    matches: Sequence[CaseMatch], expected: str, types: Mapping[str, str], threshold: float
) -> int | None:
    for position, match in enumerate(matches[:TOP_K], start=1):
        if match.similarity < threshold:
            return None
        if types.get(match.case_id) == expected:
            return position
    return None


def _phishing(results: Sequence[RowResult]) -> list[RowResult]:
    return [result for result in results if result.row["label"] == "phishing"]


def _normal(results: Sequence[RowResult]) -> list[RowResult]:
    return [result for result in results if result.row["label"] == "normal"]


def _selection_normal(results: Sequence[RowResult]) -> list[RowResult]:
    # 기준값 선택·부착률 상한에 쓰는 정상. 필기 질의응답(qa_written)은 통화 문체가 아니라 뺀다.
    return [r for r in _normal(results) if r.row.get("kind") != QA_KIND]


def _source(row: dict) -> str:
    return "합성" if row.get("synthetic", True) else "실제"


def _attach_by(normal: Sequence[RowResult], key: Callable[[dict], str], threshold: float) -> dict:
    groups: dict[str, list[RowResult]] = {}
    for result in normal:
        groups.setdefault(key(result.row), []).append(result)
    return {
        name: (sum(metrics.attached(r.matches, threshold) for r in group), len(group))
        for name, group in sorted(groups.items())
    }


def load_extra_normals(path: Path) -> list[dict]:
    """로컬 정상 평가셋의 dev/test 행만 읽는다. 원문은 메모리에서만 쓴다."""
    rows = load_rows(path)
    return [row for row in rows if row.get("label") == "normal" and row.get("split") in ("dev", "test")]


def summarize(results: Sequence[RowResult], types: Mapping[str, str], threshold: float) -> dict:
    phishing, all_normal = _phishing(results), _normal(results)
    normal = _selection_normal(results)
    ranks = [first_hit_rank(r.matches, r.row["type"], types, threshold) for r in phishing]
    type_counts = Counter(result.row["type"] for result in phishing)
    majority_type, majority = type_counts.most_common(1)[0]
    per_type = {}
    for name in sorted(type_counts):
        hits = sum(rank is not None for rank, r in zip(ranks, phishing) if r.row["type"] == name)
        per_type[name] = (hits, type_counts[name])
    return {
        "n": len(phishing),
        "groups": len({result.row["group"] for result in phishing}),
        "hit1": metrics.hit_rate(ranks, 1),
        "hit3": metrics.hit_rate(ranks, 3),
        "hits3": sum(rank is not None for rank in ranks),
        "mrr": metrics.mrr(ranks),
        # 가장 흔한 유형을 늘 맞혔다고 칠 때의 비율. hit@3 이 이보다 의미 있게 높아야 한다.
        "majority_type": majority_type,
        "majority": majority / len(phishing),
        "normal_n": len(normal),
        "attach": metrics.attach_rate([r.matches for r in normal], threshold),
        "auc": metrics.auc(
            [metrics.top1(r.matches) for r in phishing], [metrics.top1(r.matches) for r in normal]
        ),
        "per_type": per_type,
        "macro_hit3": statistics.fmean(hits / n for hits, n in per_type.values()),
        "qa_n": len(all_normal) - len(normal),
        "qa_attach": (
            metrics.attach_rate([r.matches for r in all_normal if r.row.get("kind") == QA_KIND], threshold)
            if len(all_normal) > len(normal)
            else 0.0
        ),
        "attach_by_source": _attach_by(normal, _source, threshold),
        "attach_by_kind": _attach_by(all_normal, lambda row: row.get("kind", "project"), threshold),
        "attach_by_type": _attach_by(all_normal, lambda row: row["type"], threshold),
    }


def evaluate(
    results: Sequence[RowResult],
    types: Mapping[str, str],
    *,
    include_test: bool = True,
    max_rate: float = MAX_NORMAL_RATE,
    threshold: float | None = None,
) -> dict:
    dev = [result for result in results if result.row["split"] == "dev"]
    test = [result for result in results if result.row["split"] == "test"]
    if threshold is None:
        threshold = metrics.pick_threshold(
            [result.matches for result in _selection_normal(dev)],
            max_rate,
            [metrics.top1(result.matches) for result in dev],
        )
    evaluation = {"threshold": threshold, "dev": summarize(dev, types, threshold)}
    if include_test:
        evaluation["test"] = summarize(test, types, threshold)
        misses = [
            result
            for result in _phishing(test)
            if first_hit_rank(result.matches, result.row["type"], types, threshold) is None
        ]
        evaluation["misses"] = sorted(misses, key=lambda r: -metrics.top1(r.matches))[:FAILURE_LIMIT]
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


def _excerpt(row: Mapping) -> str:
    # 리포트는 저장소에 올라간다. 재배포 허락이 없는 실제 대본은 앞부분도 싣지 않는다.
    return _cell(row["text"]) if row.get("synthetic") else "(실제 대본, 비공개)"


def _cell(text: str, limit: int = 80) -> str:
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[:limit] + "…"


def _split_lines(name: str, entry: dict) -> str:
    low, high = metrics.wilson(entry["hits3"], entry["n"])
    return (
        f"| {name} | {entry['n']} ({entry['groups']} group) | {_pct(entry['hit1'])} | "
        f"{_pct(entry['hit3'])} ({entry['hits3']}/{entry['n']}, 95% 구간 {_pct(low)}~{_pct(high)}) | "
        f"{entry['mrr']:.3f} | {_pct(entry['majority'])} | {_pct(entry['attach'])} ({entry['normal_n']}건) | "
        f"{entry['auc']:.3f} | {_pct(entry['macro_hit3'])} |"
    )


def normal_note(meta: dict) -> str:
    return REAL_NORMAL_NOTE if meta.get("real_normals") else SYNTHETIC_WARNING


def render_report(evaluation: dict, meta: dict) -> str:
    dev, test = evaluation["dev"], evaluation["test"]
    low, high = metrics.wilson(test["hits3"], test["n"])
    if low >= TARGET_HIT_AT_3:
        verdict = "충족"
    elif high < TARGET_HIT_AT_3:
        verdict = "미충족"
    else:
        verdict = "판단 보류"
    kb_counts = meta["kb_counts"]
    type_names = sorted(set(kb_counts) | set(dev["per_type"]) | set(test["per_type"]))

    def count(entry: dict, name: str) -> str:
        hits, n = entry["per_type"].get(name, (0, 0))
        return f"{hits}/{n}" if n else "—"

    lines = [
        f"# 통화 사례 검색 평가: {meta['label']}",
        "",
        f"실행일: {meta['date']} · 커밋: `{meta['commit']}` · KB: {meta['kb_total']}건 · 분할 시드: `{meta['seed']}`",
        "",
        f"설정: {meta['settings']}",
        "",
        f"질의: 평가셋 각 행의 `text`(mask_pii 적용){', 상대 발화만(turns 가 없으면 전체)' if meta.get('query_side') == 'other' else ', 대본 전체'}.",
        "",
        f"> {normal_note(meta)}",
        "",
        "## 운영 지점",
        "",
        f"- 기준값: {evaluation['threshold']:.4f} (dev 정상 통화 부착률 {_pct(MAX_NORMAL_RATE)} 이하가 되는 가장 낮은 값, 코드 값 `VOICE_MIN_SIMILARITY` = {meta['code_threshold']:.4f})",
        f"- test hit@3: {_pct(test['hit3'])} ({test['hits3']}/{test['n']}, 95% 구간 {_pct(low)}~{_pct(high)}) — 목표 {_pct(TARGET_HIT_AT_3)} {verdict}",
        f"- test 다수 유형 기준선: {_pct(test['majority'])} (`{test['majority_type']}`)",
        "",
        "## 분할별 지표",
        "",
        "| 분할 | 보이스피싱 질의 | hit@1 | hit@3 | MRR | 다수 유형 기준선 | 정상 부착률 | AUC (top-1, 보이스피싱 대비 정상) | 유형 평균 hit@3 |",
        "|---|---|---|---|---|---|---|---|---|",
        _split_lines("dev", dev),
        _split_lines("test", test),
        "",
        "## 유형별 hit@3 (건수)",
        "",
        "소수 유형은 표본이 몇 건뿐이다. 합계 hit@3 은 기관 사칭·대출 빙자가 끌어올린 값이다.",
        "",
        "| 유형 | KB | dev | test |",
        "|---|---|---|---|",
        *(f"| {name} | {kb_counts.get(name, 0)} | {count(dev, name)} | {count(test, name)} |" for name in type_names),
        "",
        "## 정상 부착률 분해",
        "",
        "출처·종류·유형별 건수는 괄호 안이다. 출처·선택 부착률은 qa_written 을 뺀 선택 정상이 분모이고, 종류별·유형별은 qa_written 을 포함한 정상 전체가 분모다.",
        "",
        f"- dev: {dev_detail(dev)}",
        f"- test: {dev_detail(test)}",
        "",
        "## 지연",
        "",
        f"- 첫 호출(KB 로드 포함): {meta['latency']['first_ms']:.1f}ms",
        f"- 질의 1건: p50 {meta['latency']['p50_ms']:.2f}ms · p95 {meta['latency']['p95_ms']:.2f}ms",
        "",
        f"## hit@3 실패 상위 {FAILURE_LIMIT}건 (test)",
        "",
    ]
    if not evaluation["misses"]:
        return "\n".join([*lines, "없음.", ""])
    lines += ["| id | 유형 | top-1 | 붙은 사례(유형) | 질의 앞부분 |", "|---|---|---|---|---|"]
    for result in evaluation["misses"]:
        top = result.matches[0] if result.matches else None
        case = f"{top.case_id} ({meta['types'].get(top.case_id, '?')})" if top else "—"
        lines.append(
            f"| {result.row['id']} | {result.row['type']} | {metrics.top1(result.matches):.4f} | {case} | {_excerpt(result.row)} |"
        )
    return "\n".join([*lines, ""])


def _commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=EVAL_DIR
        ).stdout.strip()

    # 추적 파일 변경만 dirty 로 친다(추적하지 않는 파일은 결과에 영향이 없다)
    return git("rev-parse", "--short", "HEAD") + (
        "+dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
    )


def _pairs(entries: Mapping[str, tuple[int, int]]) -> str:
    return ", ".join(f"{name} {_pct(hits / n)}({n})" for name, (hits, n) in entries.items())


def dev_detail(dev: dict) -> str:
    """정상 부착률의 출처별·종류별·유형별 분해. 집계 수치만 담는다."""
    return (
        f"출처별 {_pairs(dev['attach_by_source'])} / 종류별 {_pairs(dev['attach_by_kind'])} / "
        f"유형별 {_pairs(dev['attach_by_type'])} / 분모: 출처별은 qa_written 제외 선택 {dev['normal_n']}건, "
        f"종류별·유형별은 qa_written 포함 정상 전체 / qa_written {dev['qa_n']}건 부착 {_pct(dev['qa_attach'])}"
    )


def _ngram(value: str) -> tuple[int, ...]:
    return tuple(sorted({int(part) for part in value.split(",")}))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="통화 사례 검색 평가")
    parser.add_argument("--label", help="리포트 파일 이름에 붙일 방식 이름")
    parser.add_argument("--dev-only", action="store_true", help="dev 지표 한 줄만 출력한다")
    parser.add_argument("--ngram", type=_ngram, help="예: 2,3")
    parser.add_argument("--tf", choices=("raw", "log"), help="TF 가중: 원 빈도 또는 1+ln(tf)")
    parser.add_argument("--chunk", type=int, default=0, help="KB 대본을 이 글자 수 조각으로 나눈다(절반 겹침)")
    parser.add_argument("--query-head", type=int, default=0, help="질의 대본의 앞 N 자만 쓴다")
    parser.add_argument(
        "--query-side", choices=("all", "other"), default="all", help="other: turns 가 있는 질의는 상대 발화만 쓴다"
    )
    parser.add_argument(
        "--identity", choices=tuple(IDENTITY_SETS), help="신원 어휘가 나오면 해당 유형 사례를 앞세운다(어휘표, LLM 없음)"
    )
    parser.add_argument("--identity-mode", choices=("filter", "boost", "score"), default="score")
    parser.add_argument("--identity-boost", type=float, default=search.VOICE_IDENTITY_BOOST, help="boost 일 때 더할 유사도")
    parser.add_argument("--extra-normals", type=Path, help="로컬 정상 평가셋(jsonl). dev/test 정상 질의에 더한다")
    parser.add_argument(
        "--gate", type=float, nargs="?", const=search.VOICE_GATE_MIN, help="사례 첨부 관문 기준(값 없이 주면 운영 값)"
    )
    parser.add_argument(
        "--similarity-only", action="store_true", help="실험용: 신원 가점·관문 없이 유사도만(기본은 운영 경로)"
    )
    parser.add_argument("--max-normal-rate", type=float, default=MAX_NORMAL_RATE)
    args = parser.parse_args(argv)
    if not args.dev_only and not args.label:
        parser.error("--label 이 필요하다")

    ngram_sizes = args.ngram or search.VOICE_NGRAM_SIZES
    sublinear_tf = search.VOICE_SUBLINEAR_TF if args.tf is None else args.tf == "log"
    rows = load_rows()
    if args.extra_normals:
        rows += load_extra_normals(args.extra_normals)
    types = kb_types()
    tuned = args.ngram is not None or args.tf is not None or args.chunk
    if args.identity:
        # 기준값은 원점수 1위로 정해지므로 순서를 바꿔도 같다. 전체 순위를 받아 기준값을 정한 뒤 바꾼다.
        searcher = lambda text: rank_all(text, tuned, ngram_sizes, sublinear_tf, args.chunk)  # noqa: E731
    elif tuned:
        searcher = make_searcher(load_cases(search.VOICE_CASES_DIR), ngram_sizes, sublinear_tf, args.chunk)
    elif args.similarity_only or args.gate is not None:
        searcher = similarity_only_searcher
    else:
        # 운영 경로(search_gated: 신원 가점 + 관문). 첫 검색에 KB 로드와 색인 생성이 들어간다.
        searcher = default_searcher
    if args.dev_only:
        rows = [row for row in rows if row["split"] == "dev"]
    results = run_search(rows, searcher, args.query_head, args.query_side)
    fixed = None
    if args.identity:
        base = [RowResult(r.row, r.matches[:TOP_K], r.seconds) for r in results]
        fixed = evaluate(base, types, include_test=False, max_rate=args.max_normal_rate)["threshold"]
        results = apply_identity(
            results, types, IDENTITY_SETS[args.identity], fixed, args.identity_mode, args.identity_boost, args.query_side
        )
        if args.identity_mode == "score":
            fixed = None
    if args.gate is not None:
        # 관문이 붙일지를 바꾸므로 기준값은 다시 고른다
        results, fixed = apply_gate(results, args.gate, args.query_side), None
    evaluation = evaluate(
        results, types, include_test=not args.dev_only, max_rate=args.max_normal_rate, threshold=fixed
    )
    settings = (
        f"문자 n-gram {ngram_sizes} TF-IDF 코사인, TF {'1+ln(tf)' if sublinear_tf else '원 빈도'}, "
        f"KB 조각 {args.chunk or '없음(대본 전체)'}, 질의 {f'앞 {args.query_head}자' if args.query_head else '대본 전체'}"
        f"{', 상대 발화만(turns 없으면 전체)' if args.query_side == 'other' else ''}"
        f"{f', 신원 {args.identity} {args.identity_mode}' + f' +{args.identity_boost}' if args.identity else ''}"
        f"{f', 관문 >= {args.gate}' if args.gate is not None else ''}, "
        f"{', 운영 경로(search_gated, 관문 >= ' + str(search.VOICE_GATE_MIN) + ')' if searcher is default_searcher else ''}, "
        f"정상 부착 상한 {_pct(args.max_normal_rate)}"
    )
    if args.dev_only:
        dev = evaluation["dev"]
        print(f"<!-- {dev_detail(dev)} -->")
        print(
            f"| {settings} | {evaluation['threshold']:.4f} | {_pct(dev['hit3'])} | {_pct(dev['hit1'])} | "
            f"{dev['mrr']:.3f} | {_pct(dev['macro_hit3'])} | {_pct(dev['attach'])} | "
            + ", ".join(f"{name} {hits}/{n}" for name, (hits, n) in dev["per_type"].items())
            + " |"
        )
        return 0

    meta = {
        "label": args.label,
        "date": date.today().isoformat(),
        "commit": _commit(),
        "kb_total": len(types),
        "kb_counts": Counter(types.values()),
        "types": types,
        "seed": SEED,
        "settings": settings,
        "code_threshold": search.VOICE_MIN_SIMILARITY,
        "real_normals": any(not r.get("synthetic", True) for r in rows if r["label"] == "normal"),
        "query_side": args.query_side,
        "latency": latency(results),
    }
    path = REPORTS / f"{meta['date']}-voice-rag-{args.label}.md"
    path.write_text(render_report(evaluation, meta), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
