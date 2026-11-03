"""통화 사례 검색: TF-IDF(운영 경로)·임베딩 혼합 비교 실험.

ai/ 에서 시스템 Python(numpy 필요)으로 실행한다. 기준값·hit@3 정의는 voice_rag_eval.py 와 같다.

    python eval/embed_voice_experiment.py --alpha 0.5 --pool mean --extra-normals D:/voice/dataset/normal_real.jsonl

대본은 임베딩 모델 입력 한도를 넘으므로 조각(--chunk 자, 절반 겹침)으로 나눠 임베딩한다.
--pool mean 은 조각 벡터 평균, max 는 질의 조각·사례 조각 쌍 최댓값이다.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np

import voice_rag_eval as V
from ai.case.attach_gate import load_model
from ai.case.voice import search_by_identity
from ai.kb import search
from ai.kb.search import load_cases
from ai.types import CaseMatch
from embed_cache import EmbeddingCache

CHUNK_DEFAULT = 1500


def pieces(text: str, size: int) -> list[str]:
    return V.chunk(text, size) or [text[:size]]


def pooled(cache: EmbeddingCache, texts: Sequence[str], size: int) -> list[np.ndarray]:
    """문장마다 조각 벡터 행렬(조각 수 x 차원)을 돌려준다."""
    split = [pieces(text, size) for text in texts]
    flat = cache.embed([piece for group in split for piece in group])
    out, start = [], 0
    for group in split:
        out.append(flat[start : start + len(group)])
        start += len(group)
    return out


def mean_unit(matrix: np.ndarray) -> np.ndarray:
    vector = matrix.mean(0)
    return vector / (np.linalg.norm(vector) or 1.0)


def emb_scores(query_sets, kb_sets, pool: str) -> np.ndarray:
    if pool == "mean":
        q = np.stack([mean_unit(m) for m in query_sets])
        k = np.stack([mean_unit(m) for m in kb_sets])
        return q @ k.T
    kb_flat = np.concatenate(kb_sets)
    owner = np.concatenate([np.full(len(m), i) for i, m in enumerate(kb_sets)])
    out = np.full((len(query_sets), len(kb_sets)), -1.0, dtype=np.float32)
    for r, q in enumerate(query_sets):
        sims = (q @ kb_flat.T).max(0)
        np.maximum.at(out[r], owner, sims)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha", type=float, default=0.5, help="TF-IDF 비중. 0 이면 임베딩만")
    parser.add_argument("--pool", choices=("mean", "max"), default="mean")
    parser.add_argument("--chunk", type=int, default=CHUNK_DEFAULT)
    parser.add_argument("--no-gate", action="store_true")
    parser.add_argument("--gate-min", type=float, default=search.VOICE_GATE_MIN)
    parser.add_argument("--extra-normals", type=Path)
    parser.add_argument("--max-normal-rate", type=float, default=V.MAX_NORMAL_RATE)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args(argv)

    rows = V.load_rows()
    if args.extra_normals:
        rows += V.load_extra_normals(args.extra_normals)
    if not args.test:
        rows = [row for row in rows if row["split"] == "dev"]
    types = V.kb_types()
    cases = list(load_cases(search.VOICE_CASES_DIR))
    order = {case.case_id: i for i, case in enumerate(cases)}
    queries = [V.query_text(row) for row in rows]

    tfidf = np.zeros((len(rows), len(cases)), dtype=np.float32)
    for r, text in enumerate(queries):
        for match in search_by_identity(text, top_k=V.FULL_RANK_K, min_similarity=0.0, use_embedding=False).matches:
            tfidf[r, order[match.case_id]] = match.similarity
    cache = EmbeddingCache()
    emb = emb_scores(
        pooled(cache, queries, args.chunk), pooled(cache, [case.variants[0] for case in cases], args.chunk), args.pool
    )
    scores = args.alpha * tfidf + (1 - args.alpha) * emb
    gate = load_model()

    results = []
    for r, row in enumerate(rows):
        if not args.no_gate and gate.score(queries[r]) < args.gate_min:
            results.append(V.RowResult(row, [], 0.0))
            continue
        top = np.argsort(-scores[r])[: V.TOP_K]
        results.append(
            V.RowResult(
                row,
                [
                    CaseMatch(case_id=cases[c].case_id, similarity=round(float(scores[r, c]), 4), matched_variant="", categories=[])
                    for c in top
                ],
                0.0,
            )
        )
    evaluation = V.evaluate(results, types, include_test=args.test, max_rate=args.max_normal_rate)
    out = {"alpha": args.alpha, "pool": args.pool, "chunk": args.chunk, "gate": not args.no_gate, "threshold": evaluation["threshold"]}
    for name in ("dev", "test") if args.test else ("dev",):
        part = evaluation[name]
        out[name] = {
            "hit3": f"{part['hits3']}/{part['n']} ({part['hit3']:.1%})",
            "macro": round(part["macro_hit3"], 3),
            "attach": round(part["attach"], 3),
            "auc": round(part["auc"], 3),
            "per_type": {k: f"{h}/{n}" for k, (h, n) in part["per_type"].items()},
        }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
