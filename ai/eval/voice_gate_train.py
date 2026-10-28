"""통화 사례 첨부 관문(ai.case.attach_gate) 학습.

sklearn 이 필요하다. 운영 .venv 에는 sklearn 이 없어(새 의존성을 더하지 않는다) 시스템 Python 으로 돌린다.
ai/ 에서 실행한다. openai 등 ai 패키지 의존은 .venv 의 site-packages 를 뒤에 붙여 빌린다.

    C:/Users/ksy/AppData/Local/Programs/Python/Python312/python.exe -X utf8 eval/voice_gate_train.py

학습 데이터(배포용 가중치는 저장소에 둘 수 있는 것만):
- 양성: voice KB(ai/kb/voice_cases)의 보이스피싱 레코드.
- 음성: eval/datasets/voice_gate_train.jsonl 의 학습 전용 합성 정상 대본(split=train). dev/test 질의는 쓰지 않는다.
--extra-train 은 로컬 비교 실험 전용이다(실제 정상 통화 일부를 음성에 더한다). 이 옵션을 쓰면
저장소 경로에 쓰지 않도록 --out 을 따로 받아야 한다.

같은 입력·옵션이면 같은 가중치가 나온다(lbfgs, 고정 순서). 내보낸 뒤 고정 입력의 sklearn 점수를
출력한다. 배포 가중치를 바꾸면 tests/test_attach_gate.py 의 기대값을 이 출력으로 바꾼다.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
AI_DIR = EVAL_DIR.parent
sys.path.insert(0, str(AI_DIR / "src"))
sys.path.append(str(AI_DIR / ".venv" / "Lib" / "site-packages"))

import numpy as np  # noqa: E402
from scipy.sparse import csr_matrix, hstack  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402

from ai.case import attach_gate as gate  # noqa: E402
from ai.kb.search import VOICE_CASES_DIR, load_cases  # noqa: E402
from voice_gate_eval import PARITY_TEXTS, half_of  # noqa: E402

TRAIN_NORMALS = EVAL_DIR / "datasets" / "voice_gate_train.jsonl"
MAX_CHAR_VOCAB = 20_000
MAX_WORD_VOCAB = 4_000
MIN_DF = 2
# dev 비교(C 1·3·10, fix round 1)에서 고른 값. 배포 가중치(ai/kb/voice_gate.json)는 기본값(C=10,
# 단계 신호 계수 비음수, 독백형 학습 정상 포함)으로 만든다.
DEFAULT_C = 10.0


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def training_docs(extra: Path | None, no_monologue: bool = False) -> tuple[list[str], list[int], dict]:
    positives = [case.variants[0] for case in load_cases(VOICE_CASES_DIR)]
    negatives = [
        row["text"]
        for row in load_jsonl(TRAIN_NORMALS)
        if row["split"] == "train"
        and row["label"] == "normal"
        and not (no_monologue and row.get("kind") == "monologue")
    ]
    meta = {"positives": len(positives), "synthetic_normals": len(negatives)}
    if extra is not None:
        rows = [
            row
            for row in load_jsonl(extra)
            if row.get("label") == "normal" and row.get("split") == "dev" and half_of(row["group"]) == "train"
        ]
        negatives += [row["text"] for row in rows]
        meta["extra_normals"] = len(rows)
    return positives + negatives, [1] * len(positives) + [0] * len(negatives), meta


def build_vocab(counts: list[Counter], cap: int) -> dict[str, float]:
    df = Counter(term for doc in counts for term in doc)
    kept = sorted((term for term, n in df.items() if n >= MIN_DF), key=lambda t: (-df[t], t))[:cap]
    total = len(counts)
    return {term: float(np.log((1 + total) / (1 + df[term])) + 1.0) for term in sorted(kept)}


def matrix(vectors: list[dict[str, float]], vocab: dict[str, float]) -> csr_matrix:
    column = {term: i for i, term in enumerate(vocab)}
    rows, cols, vals = [], [], []
    for r, vec in enumerate(vectors):
        for term, value in vec.items():
            rows.append(r)
            cols.append(column[term])
            vals.append(value)
    return csr_matrix((vals, (rows, cols)), shape=(len(vectors), len(vocab)))


def featurize(texts: list[str], char_vocab: dict, word_vocab: dict, active: tuple[bool, ...]) -> csr_matrix:
    char = matrix([gate.tfidf(gate.char_counts(t), char_vocab) for t in texts], char_vocab)
    word = matrix([gate.tfidf(gate.word_counts(t), word_vocab) for t in texts], word_vocab)
    # 끈 단계 신호는 열을 0 으로 둔다. L2 규제라 그 계수는 정확히 0 으로 학습된다(추론 코드는 그대로).
    stage = csr_matrix(
        np.array([[v if on else 0.0 for v, on in zip(gate.stage_features(t), active)] for t in texts], dtype=float)
    )
    return hstack([char, word, stage]).tocsr()


def train(texts: list[str], labels: list[int], c: float, use_stage: bool = True, nonneg_stage: bool = False):
    """nonneg_stage 이면 단계 신호 계수가 음수로 나온 신호를 끄고 다시 학습한다(음수가 없을 때까지).

    단계 신호는 모두 보이스피싱 수법이라 음수 계수(= 정상 쪽 근거)는 학습 정상 대본의 어휘 치우침이다.
    """
    char_vocab = build_vocab([gate.char_counts(t) for t in texts], MAX_CHAR_VOCAB)
    word_vocab = build_vocab([gate.word_counts(t) for t in texts], MAX_WORD_VOCAB)
    active = (use_stage,) * len(gate.STAGE_NAMES)
    while True:
        x = featurize(texts, char_vocab, word_vocab, active)
        clf = LogisticRegression(C=c, class_weight="balanced", solver="lbfgs", max_iter=10_000)
        clf.fit(x, np.array(labels))
        stage_coef = clf.coef_[0][-len(gate.STAGE_NAMES) :]
        negative = [i for i, (v, on) in enumerate(zip(stage_coef, active)) if on and v < 0]
        if not nonneg_stage or not negative:
            return clf, char_vocab, word_vocab, active
        active = tuple(on and i not in negative for i, on in enumerate(active))


def export(clf, char_vocab: dict, word_vocab: dict, meta: dict) -> dict:
    coef = clf.coef_[0]
    nc, nw = len(char_vocab), len(word_vocab)
    char = {t: (idf, float(coef[i])) for i, (t, idf) in enumerate(char_vocab.items())}
    word = {t: (idf, float(coef[nc + i])) for i, (t, idf) in enumerate(word_vocab.items())}
    stage = [float(v) for v in coef[nc + nw :]]
    return gate.model_to_dict(char, word, stage, float(clf.intercept_[0]), meta)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="통화 사례 첨부 관문 학습")
    parser.add_argument("--c", type=float, default=DEFAULT_C, help="로지스틱 회귀 C(규제 역수)")
    parser.add_argument("--no-stage", action="store_true", help="수법 단계 신호 없이(비교용)")
    parser.add_argument("--signed-stage", action="store_true", help="단계 신호 계수의 음수를 허용한다(비교용, 기본은 음수 신호를 끄고 다시 학습)")
    parser.add_argument("--no-monologue", action="store_true", help="독백형 학습 정상을 빼고 학습한다(비교용)")
    parser.add_argument("--extra-train", type=Path, help="로컬 실제 정상 jsonl. dev 중 group 절반(train)을 음성에 더한다")
    parser.add_argument("--out", type=Path, default=gate.MODEL_PATH)
    args = parser.parse_args(argv)
    if args.extra_train is not None and args.out.resolve() == gate.MODEL_PATH.resolve():
        parser.error("--extra-train 가중치는 배포하지 않는다. --out 으로 저장소 밖 경로를 준다")

    texts, labels, meta = training_docs(args.extra_train, args.no_monologue)
    clf, char_vocab, word_vocab, active = train(texts, labels, args.c, not args.no_stage, not args.signed_stage)
    meta = {
        "trained_on": meta,
        "c": args.c,
        "stage": not args.no_stage,
        "nonneg_stage": not args.signed_stage,
        "stage_dropped": [n for n, on in zip(gate.STAGE_NAMES, active) if not on and not args.no_stage],
    }
    data = export(clf, char_vocab, word_vocab, meta)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{args.out} {args.out.stat().st_size / 1e6:.2f}MB char {len(char_vocab)} word {len(word_vocab)} {meta}")
    print("stage:", {k: round(v, 3) for k, v in data["stage"].items()}, "intercept", round(data["intercept"], 3))

    x = featurize(list(PARITY_TEXTS), char_vocab, word_vocab, active)
    expected = clf.predict_proba(x)[:, 1]
    print("PARITY = (")
    for text, value in zip(PARITY_TEXTS, expected):
        print(f"    {value:.10f},  # {text[:20]}")
    print(")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
