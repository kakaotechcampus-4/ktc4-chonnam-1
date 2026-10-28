"""통화 대본 데이터셋을 voice KB 와 검색 평가셋으로 나눈다.

ai/ 에서 실행한다. 입력은 저장소 밖 Task 1 산출물이다.

    python eval/voice_rag_build.py D:/voice/dataset/voice_calls.jsonl

같은 입력이면 같은 결과가 나온다. 분할을 바꾸면 KB 와 평가셋을 함께 다시 만들고
voice_rag_eval.py 로 기준값을 다시 정한다.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from ai.kb.normalize import normalize
from ai.kb.search import VOICE_CASES_DIR

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "datasets" / "voice_rag.jsonl"
SEED = "voice-rag-2026-10-28"
REVIEWER = "auto-import-2026-10-28"
COLLECTED_AT = "2026-10-28"
KB_SHARE, DEV_SHARE = 0.6, 0.2
# Task 1 은 전화번호를 남겼다. 커밋되는 KB·평가셋에는 휴대폰 번호를 남기지 않는다.
MOBILE_RE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
MOBILE_MASK = "<전화번호>"
# 있는 행에만 쓰는 선택 필드. turns 는 화자별 발화, reference_topic_url 은 "주제 참고용, 원문 대조 안 함" 링크다.
OPTIONAL_KEYS = ("turns", "reference_topic_url")
EVAL_KEYS = ("id", "label", "type", "group", "split", "source", "source_url", "license", "synthetic", "text")


def _order(group: str) -> str:
    return hashlib.sha256(f"{SEED}:{group}".encode()).hexdigest()


NEW_PREFIX = "synth2-"  # D:/voice/scripts/synth_v2.py 가 만든 소수 유형 보강 틀
NEW_MIN_EVAL = 6  # 새 틀은 유형마다 dev·test 에 이만큼 이상 둔다. 나머지(절반 이상)는 KB.


def _split_old(groups: dict[tuple[str, str], set[str]]) -> dict[str, str]:
    splits = {}
    for (label, _), members in groups.items():
        ordered = sorted(members, key=_order)
        n = len(ordered)
        if label == "normal":
            dev = (n + 1) // 2
            for i, group in enumerate(ordered):
                splits[group] = "dev" if i < dev else "test"
            continue
        # group 이 3개 이상이면 dev·test 에 하나씩은 둔다. 그보다 적으면 KB 에만 둔다.
        dev = max(1, round(DEV_SHARE * n)) if n >= 3 else 0
        test = max(1, round((1 - KB_SHARE - DEV_SHARE) * n)) if n >= 3 else 0
        kb = n - dev - test
        for i, group in enumerate(ordered):
            splits[group] = "kb" if i < kb else "dev" if i < kb + dev else "test"
    return splits


def _split_new(groups: dict[tuple[str, str], set[str]]) -> dict[str, str]:
    splits = {}
    for (label, _), members in groups.items():
        ordered = sorted(members, key=_order)
        n = len(ordered)
        if label == "normal":
            dev = (n + 1) // 2
            for i, group in enumerate(ordered):
                splits[group] = "dev" if i < dev else "test"
            continue
        # KB 가 절반 아래로 내려가지 않는 한도에서 dev·test 를 NEW_MIN_EVAL 이상 둔다.
        each = max(NEW_MIN_EVAL, round(DEV_SHARE * n))
        each = min(each, n // 4)
        for i, group in enumerate(ordered):
            splits[group] = "kb" if i < n - 2 * each else "dev" if i < n - each else "test"
    return splits


def assign_splits(rows: Sequence[dict]) -> dict[str, str]:
    """group 을 kb·dev·test 에 나눈다. 같은 group 은 한 분할에만 간다.

    유형마다 따로 나눈다. 묶어서 해시로만 자르면 group 이 몇 개뿐인 유형(가족 사칭
    3개)이 KB 나 test 에서 빠질 수 있다. 정상 통화는 KB 에 넣지 않고 dev·test 에 반씩 둔다.
    새 합성 틀(synth2-)은 기존 group 과 따로 나눈다. 새 틀이 늘어도 기존 레코드의 분할이 바뀌지 않게 한다.
    """
    old, new = defaultdict(set), defaultdict(set)
    for row in rows:
        target = new if row["group"].startswith(NEW_PREFIX) else old
        target[(row["label"], row["type"])].add(row["group"])
    return _split_old(old) | _split_new(new)


def mask(text: str) -> str:
    return MOBILE_RE.sub(MOBILE_MASK, text)


def _masked_turns(turns: Sequence[dict]) -> list[dict]:
    return [{"speaker": t["speaker"], "text": mask(t["text"])} for t in turns]


def _quoted(value: str) -> str:
    # JSON 문자열은 YAML 큰따옴표 스칼라로 그대로 읽힌다. 대본의 따옴표·콜론을 다루지 않아도 된다.
    return json.dumps(value, ensure_ascii=False)


def render_case(case_id: str, row: dict) -> str:
    text = mask(row["text"])
    lines = [
        "---",
        f"id: {case_id}",
        "status: curated",
        f"origin: {'synthetic' if row['synthetic'] else 'web_public'}",
        f"dataset_id: {_quoted(row['id'])}",
        f"group: {_quoted(row['group'])}",
        f"type: {row['type']}",
        f"type_source: {row['type_source']}",
        f"source: {_quoted(row['source'])}",
        f"source_url: {_quoted(row['source_url']) if row['source_url'] else 'null'}",
        *([f"reference_topic_url: {_quoted(row['reference_topic_url'])}"] if row.get("reference_topic_url") else []),
        f"license: {_quoted(row['license'])}",
        f"synthetic: {'true' if row['synthetic'] else 'false'}",
        f"published: {row['published'] or 'null'}",
        f"collected_at: {COLLECTED_AT}",
        f"reviewer: {REVIEWER}",
        f"normalized: {_quoted(normalize(text))}",
        "variants:",
        f"  - {_quoted(text)}",
        "categories: []",
        "---",
        "",
        "통화 대본 데이터셋(Task 1)에서 자동 등재했다. 출처 이용 조건은 license 를 따른다.",
        "",
    ]
    return "\n".join(lines)


def build(rows: Sequence[dict], cases_dir: Path, dataset: Path) -> tuple[int, int]:
    splits = assign_splits(rows)
    for old in cases_dir.glob("VC-*.md"):
        old.unlink()
    cases_dir.mkdir(parents=True, exist_ok=True)

    kb_rows = [row for row in rows if splits[row["group"]] == "kb"]
    for number, row in enumerate(kb_rows, start=1):
        case_id = f"VC-{number:04d}"
        (cases_dir / f"{case_id}.md").write_text(render_case(case_id, row), encoding="utf-8")

    eval_rows = [
        {key: mask(row[key]) if key == "text" else row.get(key) for key in EVAL_KEYS}
        | {key: _masked_turns(row[key]) if key == "turns" else row[key] for key in OPTIONAL_KEYS if row.get(key)}
        | {"split": splits[row["group"]]}
        for row in rows
        if splits[row["group"]] != "kb"
    ]
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in eval_rows), encoding="utf-8"
    )
    return len(kb_rows), len(eval_rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="voice KB·평가셋 만들기")
    parser.add_argument("source", type=Path, help="Task 1 의 voice_calls.jsonl")
    args = parser.parse_args(argv)
    rows = [
        json.loads(line)
        for line in args.source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kb, evaluated = build(rows, VOICE_CASES_DIR, DATASET)
    print(f"KB {kb}건, 평가셋 {evaluated}건", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
