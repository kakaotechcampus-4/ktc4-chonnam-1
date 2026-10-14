import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import guidance_eval as ge  # noqa: E402


def _rule(directory: Path, rule_id: str, business: str, kind: str = "verification",
          match: str = "  - {category: 택배}") -> None:
    (directory / f"{rule_id}.md").write_text(
        f"---\nid: {rule_id}\nstatus: curated\nbusiness: {business}\nkind: {kind}\n"
        f'statement: "안내"\nmatch:\n{match}\nsources:\n  - name: "공지"\n    published_at: 2024-01-01\n---\n',
        encoding="utf-8",
    )


def _row(row_id, message, expected, group="smishing", **extra):
    return {"id": row_id, "message": message, "expected_business": expected, "group": group,
            "category": "delivery", "split": "dev", **extra}


def test_classify_four_outcomes():
    assert ge.classify("delivery", ["delivery"]) == "correct"
    assert ge.classify("delivery", []) == "missing"
    assert ge.classify("delivery", ["penalty"]) == "wrong"
    assert ge.classify("delivery", ["delivery", "penalty"]) == "wrong"
    assert ge.classify("none", []) == "none_ok"
    assert ge.classify("none", ["delivery"]) == "wrong"


def test_analyze_uses_fixed_rules_only():
    facts = ge.analyze("[우체국택배] 주소 불일치로 배송이 보류되었습니다. 주소를 수정해 주세요")
    assert facts.topic == "택배"
    assert facts.brand == "우체국택배"
    assert isinstance(facts.doubts, tuple)


def test_evaluate_rows_maps_rule_business(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery"),
            _row("B", "오늘 점심 메뉴 공유합니다", "none", group="benign")]
    outcomes = {o.row["id"]: o for o in ge.evaluate_rows(rows, tmp_path)}
    assert outcomes["A"].outcome == "correct"
    assert outcomes["A"].rule_ids == ("NR-0101",)
    assert outcomes["B"].outcome == "none_ok"


def test_unknown_rule_id_counts_as_wrong(tmp_path, monkeypatch):
    _rule(tmp_path, "NR-0101", "delivery")
    monkeypatch.setattr(ge, "_rule_business", lambda norm_dir: {})
    [outcome] = ge.evaluate_rows([_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery")], tmp_path)
    assert outcome.outcome == "wrong"
    assert outcome.businesses == ("unknown",)


def test_excluded_rows_are_skipped(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery", exclude="보류")]
    assert ge.evaluate_rows(rows, tmp_path) == []


def test_action_scores_count_hits_misses_extras():
    assert ge.action_scores(["주소 입력·수정"], ("주소 입력·수정", "링크 접속")) == (1, 0, 1)
    assert ge.action_scores(["전화 응대"], ()) == (0, 1, 0)


def test_all_mode_copy_marks_matched_drafts_curated(tmp_path):
    (tmp_path / "NR-0102.md").write_text(
        "---\nid: NR-0102\nstatus: draft\nbusiness: delivery\nkind: verification\n"
        'statement: "안내"\nmatch:\n  - {category: 택배}\nsources:\n  - name: "공지"\n    published_at: 2024-01-01\n---\n',
        encoding="utf-8",
    )
    copied = ge.curated_copy(tmp_path, tmp_path / "copy")
    assert "status: curated" in (copied / "NR-0102.md").read_text(encoding="utf-8")


def test_report_has_five_sections_and_commit(tmp_path):
    _rule(tmp_path, "NR-0101", "delivery")
    rows = [_row("A", "[우체국택배] 배송 보류 안내입니다", "delivery"),
            _row("K", "[과태료] 미납 고지 확인 바랍니다", "penalty", kind="unclear_org", expected_actions=[])]
    now = ge.evaluate_rows(rows, tmp_path)
    text = ge.render_report(now, now, {"date": "2026-10-11", "commit": "abc1234", "rows": 2})
    for heading in ("## ① 확인 경로 적절성", "## ② 업무 인식", "## ③ 요구 행동 정확도",
                    "## ④ 정상 문자 안내", "## ⑤ 결정용 요약"):
        assert heading in text
    assert "abc1234" in text
    assert "고정 규칙만" in text
