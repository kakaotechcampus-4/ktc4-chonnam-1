import json
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


def test_new_synthetic_groups_split_apart_from_existing_ones():
    old = [_row(f"p{i}", "phishing", "kidnapping") for i in range(10)]
    new = [_row(f"synth2-k{i:02d}", "phishing", "kidnapping") for i in range(24)]
    new += [_row(f"synth2-n{i:02d}", "normal", "family") for i in range(12)]

    before = voice_rag_build.assign_splits(old)
    after = voice_rag_build.assign_splits(old + new)

    # 새 틀이 늘어도 기존 group 의 분할은 그대로다.
    assert all(after[g] == before[g] for g in before)
    assert after == voice_rag_build.assign_splits(list(reversed(old + new)))
    kidnap = [after[f"synth2-k{i:02d}"] for i in range(24)]
    assert kidnap.count("kb") >= 12 and kidnap.count("dev") >= 6 and kidnap.count("test") >= 6
    assert "kb" not in {after[f"synth2-n{i:02d}"] for i in range(12)}


def _kb_rows():
    import re

    from ai.kb.search import VOICE_CASES_DIR

    rows = []
    for path in sorted(VOICE_CASES_DIR.glob("VC-*.md")):
        text = path.read_text(encoding="utf-8")
        rows.append(
            {
                "group": re.search(r'^group: "(.*)"$', text, re.M).group(1),
                "type": re.search(r"^type: (\S+)$", text, re.M).group(1),
                "text": text,
            }
        )
    return rows


def test_committed_voice_data_has_no_overlap_no_normal_in_kb_and_no_restricted_source():
    dataset = [
        json.loads(line)
        for line in voice_rag_build.DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kb = _kb_rows()

    kb_groups = {row["group"] for row in kb}
    eval_groups = {row["group"] for row in dataset}
    assert kb_groups.isdisjoint(eval_groups)
    assert all(row["label"] == "phishing" for row in dataset if row["group"] in kb_groups)
    # 정상 통화는 이 시점에 KB 에 없다. KB 의 유형은 전부 피싱 유형이다.
    normal_types = {row["type"] for row in dataset if row["label"] == "normal"}
    assert normal_types and not {row["type"] for row in kb} & normal_types
    # 재배포가 금지된 출처는 프로젝트 파일에 들어오지 않는다.
    for blob in [row["text"] for row in kb] + [row["source"] + row["license"] for row in dataset]:
        assert "AI Hub" not in blob and "AIHub" not in blob and "non-vishing" not in blob


def test_new_minority_templates_meet_split_floor():
    kb = _kb_rows()
    dataset = [
        json.loads(line)
        for line in voice_rag_build.DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for kind in ("family_impersonation", "kidnapping", "account_recruitment", "other_impersonation"):
        kb_n = {r["group"] for r in kb if r["type"] == kind and r["group"].startswith("synth2-")}
        dev = {r["group"] for r in dataset if r["type"] == kind and r["split"] == "dev" and r["group"].startswith("synth2-")}
        test = {r["group"] for r in dataset if r["type"] == kind and r["split"] == "test" and r["group"].startswith("synth2-")}
        assert len(kb_n) >= len(dev) + len(test) and len(dev) >= 6 and len(test) >= 6
        assert all(400 <= len(r["text"]) <= 2000 for r in dataset if r["group"] in dev | test)


def test_new_synthetic_eval_rows_carry_turns_and_no_source_url():
    rows = [
        json.loads(line)
        for line in voice_rag_build.DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    new = [row for row in rows if row["group"].startswith("synth2-")]
    assert new
    for row in new:
        assert " ".join(t["text"] for t in row["turns"]) == row["text"]
        assert {t["speaker"] for t in row["turns"]} <= {"상대", "사용자"}
        assert row["source_url"] is None
    # 기존 행에는 선택 필드가 붙지 않는다.
    assert all("turns" not in row for row in rows if not row["group"].startswith("synth2-"))


def test_extra_normals_loaded_by_split_and_qa_written_excluded_from_selection(tmp_path):
    path = tmp_path / "extra.jsonl"
    base = {"label": "normal", "type": "bank_service", "synthetic": False}
    rows = [
        {**base, "id": "r1", "group": "r1", "split": "dev", "kind": "call", "text": "가"},
        {**base, "id": "r2", "group": "r2", "split": "dev", "kind": "qa_written", "text": "나"},
        {**base, "id": "r3", "group": "r3", "split": "train", "kind": "call", "text": "다"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    loaded = voice_rag_eval.load_extra_normals(path)

    assert [r["id"] for r in loaded] == ["r1", "r2"]


def test_threshold_ignores_qa_written_and_attach_is_split_by_source_kind_type():
    def normal(kind, score, synthetic, ntype="bank_service", group="n"):
        result = _result("dev", "normal", ntype, [_match("VC-1", score)], group)
        result.row.update({"kind": kind, "synthetic": synthetic})
        return result

    results = [
        _result("dev", "phishing", "loan_fraud", [_match("VC-1", 0.5)], "a"),
        _result("dev", "phishing", "kidnapping", [_match("VC-2", 0.5)], "b"),
        normal("call", 0.1, False, group="c"),
        normal("monologue", 0.2, False, "family", "d"),
        normal("qa_written", 0.9, False, group="e"),
        normal(None, 0.3, True, group="f"),
    ]
    results[-1].row.pop("kind")

    evaluation = voice_rag_eval.evaluate(results, TYPES, include_test=False, max_rate=0.0)
    dev = evaluation["dev"]

    # qa_written(0.9) 는 기준값 선택에 쓰지 않는다. 선택 정상 최댓값은 0.3 이라 부착 0 이 되는 가장 낮은 후보 0.5 를 고른다.
    assert evaluation["threshold"] == 0.5
    assert dev["normal_n"] == 3 and dev["qa_n"] == 1 and dev["qa_attach"] == 1.0
    assert dev["attach"] == 0.0
    assert dev["macro_hit3"] == (1.0 + 0.0) / 2
    assert dev["attach_by_source"] == {"실제": (0, 2), "합성": (0, 1)}
    assert dev["attach_by_kind"]["qa_written"] == (1, 1)
    assert dev["attach_by_type"] == {"bank_service": (1, 3), "family": (0, 1)}
