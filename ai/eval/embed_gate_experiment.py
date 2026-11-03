"""문자 사례 첨부 관문 실험: 임베딩 로지스틱 회귀로 "사기 사례 같은 문자인가"를 가른다.

ai/ 에서 시스템 Python(numpy·sklearn 필요)으로 실행한다.

    python eval/embed_gate_experiment.py --negatives D:/text/derived/gate_train_messenger.jsonl [--negatives ...] [--test]

양성은 KB 사례 variant, 음성은 평가셋과 겹치지 않는 정상 문자 학습 자료다. 평가 행은 학습에 쓰지 않는다.
관문 점수가 기준 미만이면 사례를 붙이지 않는다(통화 관문과 같은 방식). 관문 기준은 dev 에서 고르고,
유사도 기준값은 관문을 지난 결과로 rag_eval 과 같은 규칙(dev hard negative 부착 5% 이하)으로 다시 고른다.
"""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

import embed_rag_experiment as E
import rag_eval
import rag_metrics as metrics
from embed_cache import EmbeddingCache

SEED = "text-gate-2026-10-31"


def load_negatives(paths: Sequence[Path], limit: int) -> list[str]:
    texts = []
    for path in paths:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows.sort(key=lambda row: hashlib.sha256(f"{SEED}:{row['text']}".encode()).hexdigest())
        texts += [row.get("message") or row["text"] for row in rows[:limit]]
    return texts


def train_gate(cache: EmbeddingCache, cases, negatives: Sequence[str], c: float) -> LogisticRegression:
    positives = [variant for case in cases for variant in case.variants]
    x = np.vstack([cache.embed(positives), cache.embed(list(negatives))])
    y = np.r_[np.ones(len(positives)), np.zeros(len(negatives))]
    return LogisticRegression(C=c, max_iter=5000, class_weight="balanced").fit(x, y)


def gated(results, gate_scores, gate_min):
    return [
        rag_eval.RowResult(r.row, r.matches if g >= gate_min else [], r.seconds)
        for r, g in zip(results, gate_scores)
    ]


def measure(results, max_rate):
    dev = [r for r in results if metrics.is_dev(r.row)]
    threshold = metrics.pick_threshold(
        [r.matches for r in dev if r.row["group"] == "hard_negative"], max_rate, [metrics.top1(r.matches) for r in dev]
    )
    return threshold


def summary(part, threshold):
    s = rag_eval.summarize(part, threshold)
    return {
        "smishing": E.hit3(part, threshold, "smishing"),
        "no_link": E.hit3(part, threshold, "no_link"),
        "benign_attach": round(s["benign"]["attach"], 3),
        "hn_attach": round(s["hard_negative"]["attach"], 3),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--negatives", type=Path, action="append", required=True)
    parser.add_argument("--limit", type=int, default=3000, help="음성 파일마다 쓸 최대 행")
    parser.add_argument("--c", type=float, default=1.0)
    parser.add_argument("--alpha", type=float, default=0.3, help="유사도 혼합의 TF-IDF 비중")
    parser.add_argument("--gate-min", type=float, help="지정하지 않으면 dev 에서 고른다")
    parser.add_argument("--max-rate", type=float, default=rag_eval.MAX_HARD_NEGATIVE_RATE)
    parser.add_argument("--with-licensed", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args(argv)

    cases = list(E.load_cases(E.CASES_DIR))
    rows = rag_eval.drop_excluded(rag_eval.load_rows(with_licensed=args.with_licensed))
    cache = EmbeddingCache()
    scores = args.alpha * E.tfidf_matrix(cases, rows) + (1 - args.alpha) * E.score_matrix(cases, rows, cache, "raw")
    base = E.to_results(rows, cases, scores)
    gate = train_gate(cache, cases, load_negatives(args.negatives, args.limit), args.c)
    gate_scores = gate.predict_proba(cache.embed([row["message"] for row in rows]))[:, 1]

    def dev_hits(gate_min):
        results = gated(base, gate_scores, gate_min)
        threshold = measure(results, args.max_rate)
        dev = [r for r in results if metrics.is_dev(r.row) and r.row["group"] == "smishing"]
        return sum(rag_eval._rank(r, threshold) is not None for r in dev), results, threshold

    if args.gate_min is None:
        grid = [round(x, 2) for x in np.arange(0.0, 1.0, 0.05)]
        best = max(grid, key=lambda g: (dev_hits(g)[0], -g))
    else:
        best = args.gate_min
    _, results, threshold = dev_hits(best)
    out = {"alpha": args.alpha, "c": args.c, "gate_min": best, "threshold": threshold}
    for name in ("dev", "test") if args.test else ("dev",):
        part = [r for r in results if metrics.is_dev(r.row) == (name == "dev")]
        out[name] = summary(part, threshold)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
