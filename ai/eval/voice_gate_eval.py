"""통화 사례 첨부 관문 비교(dev 전용). test 는 보지 않는다.

ai/ 에서 실행한다(운영 .venv 로 충분하다. 관문 추론은 순수 Python).

    python eval/voice_gate_eval.py --extra-normals <로컬 정상 jsonl> --cache <캐시 json> \
        [--model <관문 json> ...] [--contrast] [--eval-half]

검색은 운영과 같다(지인 어휘 가점 VOICE_IDENTITY_BOOST 포함, 상위 10개). 검색 결과는 --cache 에
한 번 저장하고 다시 쓴다(실제 정상 포함 dev 검색은 몇 분 걸린다). 캐시에는 질의 id·사례 id·점수만 있다.

비교하는 방식(모두 사례를 "붙일지"만 바꾼다. 판정은 바꾸지 않는다):
- 기준선: 유사도 기준값 하나.
- 관문: 관문 점수 < g 이면 붙이지 않는다. g 마다 유사도 기준값을 다시 고른다(정상 부착 5% 상한).
- 대조: 학습 전용 합성 정상 대본을 따로 색인해, 정상 사례가 보이스피싱 사례보다 위(top-1)거나
  상위 3개 중 다수면 붙이지 않는다.
- 관문 + 대조.
선택: dev 정상 부착률(qa_written 제외) <= 5% 에서 hit@3, 다음으로 유형 평균 hit@3 이 가장 높은 설정.
--eval-half 는 실제 정상 dev 중 group 절반(eval)만 잰다(나머지 절반으로 학습한 로컬 실험과 같은 표본).
"""

import argparse
import bisect
import hashlib
import json
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path

import voice_rag_eval as vre
from ai.case.attach_gate import load_model
from ai.case.voice import IDENTITY_PREFERRED_TYPES
from ai.case.lexicon import identity_kinds
from ai.kb import search
from ai.kb.normalize import normalize
from ai.kb.search import Case, build_index, rank, search_voice_cases
from ai.types import CaseMatch

TRAIN_NORMALS = vre.EVAL_DIR / "datasets" / "voice_gate_train.jsonl"
CACHE_K = 10
HALF_SEED = "voice-gate-half-2026-10-28"
GATE_GRID = tuple(round(i / 100, 2) for i in range(0, 100))

# 관문 패리티 입력(voice_gate_train.py 가 sklearn 점수를 출력하고 tests/test_attach_gate.py 가 대조한다).
PARITY_TEXTS = (
    "서울중앙지검 수사관입니다. 고객님 명의로 대포통장이 개설되어 사건에 연루되셨습니다. 지금 끊지 마시고 안전계좌로 옮기세요.",
    "엄마 나 폰 액정이 깨져서 수리 맡겼어. 이 번호로 문자 줘. 급하게 상품권 좀 사서 핀번호 보내 줘.",
    "네 고객님 카드 분실 신고 접수되었습니다. 재발급 카드는 등록된 주소로 오 일 안에 배송됩니다.",
    "안녕하세요 택배 기사입니다. 문 앞에 두고 사진 찍어서 보내 드릴게요.",
    "",
)


def half_of(group: str) -> str:
    """로컬 실제 정상 dev 를 group 기준 둘로 나눈다(train/eval). voice_gate_train.py 와 같이 쓴다."""
    return "train" if int(hashlib.sha256(f"{HALF_SEED}:{group}".encode()).hexdigest(), 16) % 2 == 0 else "eval"


def dev_rows(extra: Path | None, eval_half: bool) -> list[dict]:
    rows = [r for r in vre.load_rows() if r["split"] == "dev"]
    if extra:
        real = [r for r in vre.load_extra_normals(extra) if r["split"] == "dev"]
        if eval_half:
            real = [r for r in real if half_of(r["group"]) == "eval"]
        rows += real
    return rows


def _key(row: dict) -> str:
    return f"{row['id']}:{len(row['text'])}"


def _search(text: str) -> list[CaseMatch]:
    preferred = frozenset().union(*(IDENTITY_PREFERRED_TYPES.get(k, frozenset()) for k in identity_kinds(text)))
    return search_voice_cases(text, min_similarity=0.0, top_k=CACHE_K, preferred_types=preferred).matches


def normal_index():
    rows = [json.loads(line) for line in TRAIN_NORMALS.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [Case(r["id"], (r["text"],), (normalize(r["text"]),), ()) for r in rows]
    return build_index(cases, ngram_sizes=search.VOICE_NGRAM_SIZES, sublinear_tf=search.VOICE_SUBLINEAR_TF)


def cached_search(rows: Sequence[dict], cache_path: Path, contrast: bool) -> dict:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {"kb": {}, "normal": {}}
    missing = [r for r in rows if _key(r) not in cache["kb"]]
    for row in missing:
        cache["kb"][_key(row)] = [[m.case_id, m.similarity] for m in _search(row["text"])]
    missing_n = [r for r in rows if _key(r) not in cache["normal"]] if contrast else []
    if missing_n:
        index = normal_index()
        for row in missing_n:
            cache["normal"][_key(row)] = [m.similarity for m in rank(index, row["text"])[:3]]
    if missing or missing_n:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def contrast_block(kb: list, normal: list[float], rule: str) -> bool:
    if not kb:
        return False
    merged = sorted([(s, "p") for _, s in kb[:3]] + [(s, "n") for s in normal], reverse=True)[:3]
    if rule == "top1":
        return merged[0][1] == "n"
    return sum(kind == "n" for _, kind in merged) >= 2


def select(rows, kb, types, blocked: Callable[[dict], bool], max_rate: float) -> tuple[float, float, float, float]:
    """막힌 행은 사례 없음으로 친다. 정상 부착 상한을 지키는 가장 낮은 기준값과 그때 hit@3·유형 평균·부착률."""
    normal = [r for r in rows if r["label"] == "normal" and r.get("kind") != vre.QA_KIND]
    open_s1 = sorted(kb[_key(r)][0][1] for r in normal if kb[_key(r)] and not blocked(r))
    candidates = sorted({0.0 if blocked(r) or not kb[_key(r)] else kb[_key(r)][0][1] for r in rows})
    limit = max_rate * len(normal)
    threshold = None
    for value in candidates:
        if len(open_s1) - bisect.bisect_left(open_s1, value) <= limit:
            threshold = value
            break
    if threshold is None:
        threshold = round(open_s1[-1] + 0.0001, 4)
    attach = (len(open_s1) - bisect.bisect_left(open_s1, threshold)) / len(normal)
    per_type: dict[str, list[int]] = {}
    for row in rows:
        if row["label"] != "phishing":
            continue
        hit = not blocked(row) and any(
            types.get(cid) == row["type"] and sim >= threshold for cid, sim in kb[_key(row)][:3]
        )
        per_type.setdefault(row["type"], [0, 0])
        per_type[row["type"]][0] += hit
        per_type[row["type"]][1] += 1
    hits, n = sum(h for h, _ in per_type.values()), sum(c for _, c in per_type.values())
    return threshold, hits / n, statistics.fmean(h / c for h, c in per_type.values()), attach


def full_line(name: str, rows, kb, types, blocked, threshold: float) -> tuple[str, str]:
    results = [
        vre.RowResult(
            row,
            [] if blocked(row) else [CaseMatch(case_id=c, similarity=s, matched_variant="") for c, s in kb[_key(row)][:3]],
            0.0,
        )
        for row in rows
    ]
    dev = vre.evaluate(results, types, include_test=False, threshold=threshold)["dev"]
    line = (
        f"| {name} | {threshold:.4f} | {vre._pct(dev['hit3'])} | {vre._pct(dev['hit1'])} | {dev['mrr']:.3f} | "
        f"{vre._pct(dev['macro_hit3'])} | {vre._pct(dev['attach'])} | "
        + ", ".join(f"{t} {h}/{n}" for t, (h, n) in dev["per_type"].items())
        + " |"
    )
    return line, vre.dev_detail(dev)


def best_gate(rows, kb, types, scores: dict, max_rate: float, extra_block=None):
    best = None
    for g in GATE_GRID:
        def blocked(r, g=g):
            return scores[_key(r)] < g or (extra_block is not None and extra_block(r))

        t, hit3, macro, attach = select(rows, kb, types, blocked, max_rate)
        if best is None or (round(hit3, 6), round(macro, 6)) > (round(best[2], 6), round(best[3], 6)):
            best = (g, t, hit3, macro, attach, blocked)
    return best


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="통화 사례 첨부 관문 비교(dev)")
    parser.add_argument("--extra-normals", type=Path)
    parser.add_argument("--cache", type=Path, required=True, help="검색 결과 캐시(json). 저장소 밖에 둔다")
    parser.add_argument("--model", type=Path, action="append", default=[], help="관문 json. 여러 번 줄 수 있다")
    parser.add_argument("--contrast", action="store_true")
    parser.add_argument("--eval-half", action="store_true", help="실제 정상 dev 의 eval 절반만 잰다")
    parser.add_argument("--max-normal-rate", type=float, default=vre.MAX_NORMAL_RATE)
    parser.add_argument("--curve", action="store_true", help="관문 기준별 줄을 모두 출력한다")
    args = parser.parse_args(argv)

    rows = dev_rows(args.extra_normals, args.eval_half)
    types = vre.kb_types()
    cache = cached_search(rows, args.cache, args.contrast)
    kb, normal = cache["kb"], cache["normal"]
    n_norm = sum(r["label"] == "normal" and r.get("kind") != vre.QA_KIND for r in rows)
    print(f"<!-- dev 보이스피싱 {sum(r['label'] == 'phishing' for r in rows)}건, 선택 정상 {n_norm}건 -->")

    def never(_):
        return False

    t, *_ = select(rows, kb, types, never, args.max_normal_rate)
    line, detail = full_line("기준선(유사도만)", rows, kb, types, never, t)
    print(f"<!-- {detail} -->\n{line}")

    contrast_rules = {}
    if args.contrast:
        for rule in ("top1", "majority"):
            def blocked(r, rule=rule):
                return contrast_block(kb[_key(r)], normal[_key(r)], rule)

            contrast_rules[rule] = blocked
            t, *_ = select(rows, kb, types, blocked, args.max_normal_rate)
            line, detail = full_line(f"대조 {rule}", rows, kb, types, blocked, t)
            print(f"<!-- {detail} -->\n{line}")

    for path in args.model:
        model = load_model(path)
        scores = {_key(r): model.score(r["text"]) for r in rows}
        variants = [("", None)] + [(f" + 대조 {k}", b) for k, b in contrast_rules.items()]
        for suffix, extra in variants:
            g, t, hit3, macro, attach, blocked = best_gate(rows, kb, types, scores, args.max_normal_rate, extra)
            line, detail = full_line(f"관문 {path.stem} g={g:.2f}{suffix}", rows, kb, types, blocked, t)
            print(f"<!-- {detail} -->\n{line}")
            if args.curve and not suffix:
                for gg in GATE_GRID[::5]:
                    tt, h, m, a = select(rows, kb, types, lambda r, gg=gg: scores[_key(r)] < gg, args.max_normal_rate)
                    print(f"<!-- curve g={gg:.2f} t={tt:.4f} hit3={h:.3f} macro={m:.3f} attach={a:.3f} -->")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
