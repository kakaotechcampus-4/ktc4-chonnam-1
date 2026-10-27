import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import voice_rag_build  # noqa: E402
import voice_rag_eval  # noqa: E402
from ai.types import CaseMatch  # noqa: E402

TYPES = {"VC-1": "loan_fraud", "VC-2": "institution_impersonation", "VC-3": "kidnapping"}


def _match(case_id: str, score: float) -> CaseMatch:
    return CaseMatch(case_id=case_id, similarity=score, matched_variant="대본")


def _result(split: str, label: str, kind: str, matches: list[CaseMatch], group: str = "g"):
    row = {"id": f"{group}-{kind}", "split": split, "label": label, "type": kind, "group": group}
    return voice_rag_eval.RowResult(row, matches, 0.0)


def test_hit_rank_uses_kb_type_and_threshold():
    matches = [_match("VC-2", 0.5), _match("VC-1", 0.3), _match("VC-3", 0.1)]

    assert voice_rag_eval.first_hit_rank(matches, "loan_fraud", TYPES, 0.2) == 2
    # 기준값 밑의 사례는 없는 것으로 친다.
    assert voice_rag_eval.first_hit_rank(matches, "kidnapping", TYPES, 0.2) is None
    assert voice_rag_eval.first_hit_rank(matches, "kidnapping", TYPES, 0.0) == 3


def test_evaluate_picks_threshold_on_dev_normal_and_reports_majority_baseline():
    results = [
        _result("dev", "phishing", "loan_fraud", [_match("VC-1", 0.4)], "a"),
        _result("dev", "phishing", "loan_fraud", [_match("VC-2", 0.4)], "b"),
        _result("dev", "phishing", "kidnapping", [_match("VC-3", 0.4)], "c"),
        _result("dev", "normal", "bank_service", [_match("VC-1", 0.2)], "d"),
        _result("test", "phishing", "loan_fraud", [_match("VC-1", 0.15)], "e"),
        _result("test", "normal", "family", [_match("VC-1", 0.9)], "f"),
    ]

    evaluation = voice_rag_eval.evaluate(results, TYPES, max_rate=0.0)

    assert evaluation["threshold"] == 0.4
    dev = evaluation["dev"]
    assert dev["hit3"] == 2 / 3
    assert dev["majority_type"] == "loan_fraud" and dev["majority"] == 2 / 3
    assert dev["per_type"] == {"kidnapping": (1, 1), "loan_fraud": (1, 2)}
    assert dev["attach"] == 0.0
    # test 는 dev 에서 정한 기준값으로만 잰다.
    assert evaluation["test"]["hit3"] == 0.0
    assert evaluation["test"]["attach"] == 1.0


def test_chunks_cover_text_without_splitting_words():
    text = "가나다 라마바 사아자 차카타 파하"

    pieces = voice_rag_eval.chunk(text, 8)

    assert pieces[0].startswith("가나다") and pieces[-1].endswith("파하")
    assert all(word in text.split() for piece in pieces for word in piece.split())


def _row(group: str, label: str, kind: str) -> dict:
    return {"group": group, "label": label, "type": kind}


def test_splits_keep_groups_whole_and_normal_out_of_kb():
    rows = [_row(f"p{i}", "phishing", "loan_fraud") for i in range(10)]
    rows += [_row("p0", "phishing", "loan_fraud")]
    rows += [_row(f"f{i}", "phishing", "family_impersonation") for i in range(3)]
    rows += [_row(f"n{i}", "normal", "bank_service") for i in range(4)]

    splits = voice_rag_build.assign_splits(rows)

    assert splits == voice_rag_build.assign_splits(list(reversed(rows)))
    assert [splits[f"p{i}"] for i in range(10)].count("kb") == 6
    # group 이 셋뿐인 유형도 KB·dev·test 에 하나씩 둔다.
    assert sorted(splits[f"f{i}"] for i in range(3)) == ["dev", "kb", "test"]
    assert sorted(splits[f"n{i}"] for i in range(4)) == ["dev", "dev", "test", "test"]


def test_mask_hides_mobile_numbers():
    assert voice_rag_build.mask("번호는 010-1234-5678입니다") == "번호는 <전화번호>입니다"
