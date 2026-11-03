"""AI Hub 「SNS 데이터 고도화」(71343) 메신저 대화에서 정상 문자 평가 행을 뽑는다.

ai/ 에서 실행한다. 입력은 저장소 밖에 받아 둔 라벨링 데이터 zip 이다.

    python eval/messenger_normal_build.py D:/text/raw/aihub/71343/VL.zip

AI Hub 데이터는 재배포가 금지라서 결과(`datasets/rag_testset_licensed.jsonl`)는 커밋하지 않는다.
rag_eval.py 에 --with-licensed 를 주면 rag_testset.jsonl 과 함께 읽는다.

가족·친구·동료/지인 주제 대화에서 대화마다 한 사람이 이어 보낸 말 한 묶음을 문자 하나로 본다.
폰·번호·돈·인증 같은 말이 들어간 묶음은 hard_negative(링크 없는 지인 사칭과 겉모양이 비슷한
정상 문자), 나머지는 benign 이다. 같은 입력이면 같은 결과가 나온다.
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
OUTPUT = EVAL_DIR / "datasets" / "rag_testset_licensed.jsonl"
SEED = "messenger-normal-2026-10-31"
SOURCE_PREFIX = "aihub-71343"
TOPIC_RE = re.compile(r"관계_(가족|친구|동료)")
HARD_NEGATIVE_RE = re.compile(r"폰|핸드폰|휴대폰|액정|번호|돈|보내|송금|계좌|입금|카드|인증|급해|빌려|만원|카톡")
# 데이터의 가림 표기. #@이름# 같은 자리표시는 <이름> 으로 바꾸고, 사진·이모티콘 같은 시스템 표기가 든 묶음은 버린다.
TAG_RE = re.compile(r"#@([^#\s]+)#")
SYSTEM_TAG = "#@시스템"
# 커밋하지 않는 파일이지만 평가 질의 형태(주소 없는 본문)와 개인정보 기준은 공개 평가셋과 맞춘다.
SKIP_RE = re.compile(r"[a-z]{3,6}://|www\.|\d{8,}|01[016789][-\s]?\d{3,4}[-\s]?\d{4}", re.IGNORECASE)
MIN_CHARS, MAX_CHARS = 10, 200
DEFAULT_PER_GROUP = 300


def _order(key: str) -> str:
    return hashlib.sha256(f"{SEED}:{key}".encode()).hexdigest()


def blocks(body: Sequence[dict]) -> Iterator[str]:
    """한 사람이 이어서 보낸 발화를 공백으로 이어 붙인 묶음을 순서대로 낸다."""
    speaker, parts = None, []
    for utterance in body:
        if utterance["participantID"] != speaker and parts:
            yield " ".join(parts)
            parts = []
        speaker = utterance["participantID"]
        parts.append(utterance["utterance"].strip())
    if parts:
        yield " ".join(parts)


def pick(dialogue: dict) -> tuple[str, str] | None:
    """(group, text). hard_negative 후보가 있으면 그것을, 없으면 첫 benign 후보를 고른다."""
    first_benign = None
    for block in blocks(dialogue["body"]):
        if SYSTEM_TAG in block:
            continue
        text = TAG_RE.sub(lambda m: f"<{m.group(1)}>", block)
        if not MIN_CHARS <= len(text) <= MAX_CHARS or SKIP_RE.search(text):
            continue
        if HARD_NEGATIVE_RE.search(text):
            return "hard_negative", text
        first_benign = first_benign or text
    return ("benign", first_benign) if first_benign else None


def candidates(archive: Path) -> Iterator[dict]:
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            dialogue = json.loads(zf.read(name).decode("utf-8"))
            info = dialogue["header"]["dialogueInfo"]
            topics = [topic for topic in info["multi_topic"] if TOPIC_RE.search(topic)]
            if not topics:
                continue
            picked = pick(dialogue)
            if picked is None:
                continue
            group, text = picked
            yield {"dialogue_id": info["dialogueID"], "topic": topics[0], "group": group, "text": text}


def build(archive: Path, per_group: int) -> list[dict]:
    found = sorted(candidates(archive), key=lambda row: _order(row["dialogue_id"]))
    rows, counts = [], {"benign": 0, "hard_negative": 0}
    for row in found:
        group = row["group"]
        if counts[group] >= per_group:
            continue
        counts[group] += 1
        rows.append(
            {
                "id": f"M-{group}-{counts[group]:04d}",
                "group": group,
                "category": "acquaintance_impersonation",
                "expected_business": "none",
                "text": row["text"],
                "message": row["text"],
                "origin": "aihub_licensed",
                "source": f"{SOURCE_PREFIX}:{row['dialogue_id']}",
                "template": None,
                # 대화 단위로 나눈다. 대화마다 한 행이라 묶음이 갈라질 일은 없다.
                "split": "dev" if int(_order("split:" + row["dialogue_id"])[:8], 16) % 2 == 0 else "test",
                "topic": row["topic"],
            }
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Hub 메신저 대화 → 정상 문자 평가 행")
    parser.add_argument("archive", type=Path, help="71343 라벨링 데이터 zip (예: VL.zip)")
    parser.add_argument("--per-group", type=int, default=DEFAULT_PER_GROUP, help="benign·hard_negative 각각 최대 건수")
    args = parser.parse_args(argv)
    rows = build(args.archive, args.per_group)
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"{OUTPUT.name}: {len(rows)}행", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
