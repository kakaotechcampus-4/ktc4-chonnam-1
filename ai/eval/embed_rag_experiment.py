"""문자 사례 검색: TF-IDF·임베딩·혼합 비교 실험.

ai/ 에서 시스템 Python(numpy 필요)으로 실행한다. 기준값·hit@3 정의는 rag_eval.py 와 같다.

    python eval/embed_rag_experiment.py --mode mix --alpha 0.3 [--test]

시나리오: 링크 없는 사칭(2026-10-31 추가 23행)과 나머지 스미싱을 따로 센다.
"""

import argparse
import json
from collections.abc import Sequence

import numpy as np

import rag_eval
import rag_metrics as metrics
from ai.kb.normalize import normalize
from ai.kb.search import CASES_DIR, build_index, load_cases, rank
from ai.types import CaseMatch
from embed_cache import EmbeddingCache

# eval/datasets/README.md "링크 없는 사칭 문자 (2026-10-31)" 의 평가 23행
NO_LINK_IDS = frozenset(
    [f"S-acquaintance_impersonation-{n}" for n in range(10, 25)]
    + [f"S-card_or_account-{n}" for n in (19, 20, 21)]
    + ["S-payment-18", "S-public_support-06", "S-other-01", "S-other-02", "S-other-03"]
)


def scenario(row: dict) -> str:
    return "no_link" if row["id"] in NO_LINK_IDS else "smishing"


def embed_text(text: str, how: str) -> str:
    return normalize(text) if how == "norm" else text


def score_matrix(cases, rows, cache: EmbeddingCache, how: str):
    variants = [(i, v) for i, case in enumerate(cases) for v in case.variants]
    kb = cache.embed([embed_text(v, how) for _, v in variants])
    queries = cache.embed([embed_text(row["message"], how) for row in rows])
    sims = queries @ kb.T
    owner = np.array([i for i, _ in variants])
    emb = np.full((len(rows), len(cases)), -1.0, dtype=np.float32)
    for column, case_index in enumerate(owner):
        emb[:, case_index] = np.maximum(emb[:, case_index], sims[:, column])
    return emb


def tfidf_matrix(cases, rows):
    index = build_index(cases)
    order = {case.case_id: i for i, case in enumerate(cases)}
    out = np.zeros((len(rows), len(cases)), dtype=np.float32)
    for r, row in enumerate(rows):
        for match in rank(index, row["message"]):
            out[r, order[match.case_id]] = match.similarity
    return out


def fuse(tfidf: np.ndarray, emb: np.ndarray, mode: str, alpha: float, k: int) -> np.ndarray:
    if mode == "tfidf":
        return tfidf
    if mode == "emb":
        return emb
    if mode == "mix":
        return alpha * tfidf + (1 - alpha) * emb
    if mode == "rrf":
        # 순위만 쓰므로 기준값 비교가 가능한 점수가 아니다. 1위 점수로 부착을 가른다.
        ranks_t = (-tfidf).argsort(1).argsort(1) + 1
        ranks_e = (-emb).argsort(1).argsort(1) + 1
        return 1 / (k + ranks_t) + 1 / (k + ranks_e)
    raise ValueError(mode)


def to_results(rows, cases, scores) -> list[rag_eval.RowResult]:
    results = []
    for r, row in enumerate(rows):
        top = np.argsort(-scores[r])[: rag_eval.TOP_K]
        matches = [
            CaseMatch(
                case_id=cases[c].case_id,
                similarity=round(float(scores[r, c]), 4),
                matched_variant=cases[c].variants[0],
                categories=list(cases[c].categories),
            )
            for c in top
        ]
        results.append(rag_eval.RowResult(row, matches, 0.0))
    return results


def hit3(results: Sequence[rag_eval.RowResult], threshold: float, name: str) -> str:
    part = [r for r in results if r.row["group"] == "smishing" and scenario(r.row) == name]
    hits = sum(rag_eval._rank(r, threshold) is not None for r in part)
    return f"{hits}/{len(part)} ({hits / len(part):.1%})" if part else "-"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("tfidf", "emb", "mix", "rrf"), default="mix")
    parser.add_argument("--alpha", type=float, default=0.3)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--text", choices=("raw", "norm"), default="raw")
    parser.add_argument("--max-rate", type=float, default=rag_eval.MAX_HARD_NEGATIVE_RATE)
    parser.add_argument("--with-licensed", action="store_true")
    parser.add_argument("--test", action="store_true", help="test 분할도 출력한다(설정을 정한 뒤 한 번만)")
    args = parser.parse_args(argv)

    cases = list(load_cases(CASES_DIR))
    rows = rag_eval.drop_excluded(rag_eval.load_rows(with_licensed=args.with_licensed))
    tfidf = tfidf_matrix(cases, rows)
    emb = np.zeros_like(tfidf) if args.mode == "tfidf" else score_matrix(cases, rows, EmbeddingCache(), args.text)
    scores = fuse(tfidf, emb, args.mode, args.alpha, args.rrf_k)
    results = to_results(rows, cases, scores)
    dev = [r for r in results if metrics.is_dev(r.row)]
    test = [r for r in results if not metrics.is_dev(r.row)]
    threshold = metrics.pick_threshold(
        [r.matches for r in dev if r.row["group"] == "hard_negative"],
        args.max_rate,
        [metrics.top1(r.matches) for r in dev],
    )
    out = {"mode": args.mode, "alpha": args.alpha, "threshold": threshold}
    for name, part in (("dev", dev), ("test", test)) if args.test else (("dev", dev),):
        summary = rag_eval.summarize(part, threshold)
        out[name] = {
            "smishing": hit3(part, threshold, "smishing"),
            "no_link": hit3(part, threshold, "no_link"),
            "all_hit3": round(summary["smishing"]["hit3"], 3),
            "benign_attach": round(summary["benign"]["attach"], 3),
            "hn_attach": round(summary["hard_negative"]["attach"], 3),
            "auc_hn": round(summary["hard_negative"].get("auc", 0), 3),
        }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
