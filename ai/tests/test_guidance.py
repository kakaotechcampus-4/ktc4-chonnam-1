"""업무별 공식 안내 고르기. 판정과 무관하며 정해진 값만 본다."""

from pathlib import Path

import pytest

from ai.kb.guidance import MAX_GUIDANCE, Guidance, find_guidance, load_rules
from ai.types import AnalysisResponse


def _write_rule(directory: Path, rule_id: str, *, kind: str = "verification",
                status: str = "curated", match: str | None = "  - {category: 택배}",
                statement: str = "안내 문장") -> None:
    match_block = f"match:\n{match}\n" if match is not None else ""
    (directory / f"{rule_id}.md").write_text(
        "---\n"
        f"id: {rule_id}\n"
        f"status: {status}\n"
        "business: delivery\n"
        f"kind: {kind}\n"
        f'statement: "{statement}"\n'
        f"{match_block}"
        "sources:\n"
        '  - name: "출처 공지"\n'
        "    published_at: 2024-03-06\n"
        "---\n",
        encoding="utf-8",
    )


def _result(*, brand=None, category=None, doubts=(), env_brand=None,
            env_category=None, official="not_registered") -> dict:
    return {
        "url": {"final_url": "https://x.example", "domain": "x.example",
                "official": official, "scan": {"score": None, "scanned_at": None}},
        "message": {
            "brand": brand, "category": category, "answer": "no_risk_found",
            "details": {"doubts": [{"value": d, "evidence": "근거 문장"} for d in doubts],
                        "signals": [], "reason": {"text": "설명", "failures": []}},
        },
        "env": {
            "brand": env_brand, "category": env_category, "answer": "not_run",
            "collected_at": None,
            "details": {"doubts": [], "signals": [],
                        "reason": {"text": "페이지 미확인", "failures": ["missing_result"]}},
        },
        "result": False,
    }


def test_category_match_returns_guidance_fields(tmp_path):
    _write_rule(tmp_path, "NR-0101", statement="택배사 고객센터에 직접 확인하세요.")
    assert find_guidance(_result(category="택배"), tmp_path) == [
        Guidance(kind="verification", text="택배사 고객센터에 직접 확인하세요.",
                 source="출처 공지", published_at="2024-03-06", rule_id="NR-0101"),
    ]


def test_all_keys_in_one_condition_must_match(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {category: 금융, doubts: 전화 응대}")
    assert find_guidance(_result(category="금융"), tmp_path) == []
    assert [g.rule_id for g in find_guidance(
        _result(category="금융", doubts=["전화 응대"]), tmp_path)] == ["NR-0101"]


def test_any_condition_in_list_matches(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 한진택배}\n  - {brand: 롯데택배}")
    assert [g.rule_id for g in find_guidance(_result(brand="롯데택배"), tmp_path)] == ["NR-0101"]


def test_env_values_also_match(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    assert [g.rule_id for g in find_guidance(_result(env_brand="우체국택배"), tmp_path)] == ["NR-0101"]


def test_same_rule_not_duplicated_across_parts(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    hits = find_guidance(_result(brand="우체국택배", env_brand="우체국택배"), tmp_path)
    assert [g.rule_id for g in hits] == ["NR-0101"]


def test_draft_impersonation_and_unmatched_rules_are_excluded(tmp_path):
    _write_rule(tmp_path, "NR-0101", status="draft")
    _write_rule(tmp_path, "NR-0102", kind="impersonation")
    _write_rule(tmp_path, "NR-0103", match=None)
    assert find_guidance(_result(category="택배"), tmp_path) == []


def test_verification_first_then_rule_id_and_at_most_three(tmp_path):
    _write_rule(tmp_path, "NR-0101", kind="procedure")
    _write_rule(tmp_path, "NR-0102", kind="verification")
    _write_rule(tmp_path, "NR-0103", kind="procedure")
    _write_rule(tmp_path, "NR-0104", kind="procedure")
    hits = find_guidance(_result(category="택배"), tmp_path)
    assert len(hits) == MAX_GUIDANCE == 3
    assert [g.rule_id for g in hits] == ["NR-0102", "NR-0101", "NR-0103"]


def test_unknown_business_returns_empty(tmp_path):
    _write_rule(tmp_path, "NR-0101")
    assert find_guidance(_result(category="공공기관"), tmp_path) == []


@pytest.mark.parametrize("result", [
    None, "문자열", 42, {}, {"message": None, "env": None},
    {"message": {"details": None}}, {"message": {"details": {"doubts": "아님"}}},
    {"message": {"brand": 123, "category": ["택배"]}},
])
def test_missing_parts_return_empty(tmp_path, result):
    _write_rule(tmp_path, "NR-0101")
    assert find_guidance(result, tmp_path) == []


def test_missing_norm_dir_returns_empty(tmp_path):
    assert find_guidance(_result(category="택배"), tmp_path / "없음") == []


def test_broken_rule_file_is_skipped(tmp_path):
    (tmp_path / "NR-0100.md").write_text("---\nid: [깨진\n---\n", encoding="utf-8")
    _write_rule(tmp_path, "NR-0101", match="  {category: 택배}")      # 목록이 아님
    _write_rule(tmp_path, "NR-0102", match="  - {sender: 우체국}")   # 모르는 키
    _write_rule(tmp_path, "NR-0103")
    assert [g.rule_id for g in find_guidance(_result(category="택배"), tmp_path)] == ["NR-0103"]


def test_accepts_pydantic_model(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {brand: 우체국택배}")
    raw = _result(brand="우체국택배", category="택배")
    model = AnalysisResponse.model_validate(raw)
    assert find_guidance(model, tmp_path) == find_guidance(raw, tmp_path)
    assert [g.rule_id for g in find_guidance(model, tmp_path)] == ["NR-0101"]


def test_undecodable_or_bad_date_file_is_skipped(tmp_path):
    (tmp_path / "NR-0100.md").write_bytes(b"---\nid: NR-0100\nstatus: curated\n\xff\n---\n")
    _write_rule(tmp_path, "NR-0101")
    text = (tmp_path / "NR-0101.md").read_text(encoding="utf-8").replace("2024-03-06", "2024-13-45")
    (tmp_path / "NR-0102.md").write_text(text.replace("NR-0101", "NR-0102"), encoding="utf-8")
    _write_rule(tmp_path, "NR-0103")
    assert [g.rule_id for g in find_guidance(_result(category="택배"), tmp_path)] == ["NR-0101", "NR-0103"]


def test_non_string_match_value_skips_rule(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {category: }")
    _write_rule(tmp_path, "NR-0102", match="  - {category: yes}")
    _write_rule(tmp_path, "NR-0103")
    assert [g.rule_id for g in find_guidance(_result(category="택배"), tmp_path)] == ["NR-0103"]


def test_non_string_match_value_is_not_loaded(tmp_path):
    _write_rule(tmp_path, "NR-0101", match="  - {category: }")
    _write_rule(tmp_path, "NR-0103")
    assert [r.guidance.rule_id for r in load_rules(tmp_path)] == ["NR-0103"]


from ai.kb.guidance import NORM_DIR


def _curated_copy(tmp_path: Path) -> Path:
    # 검토 전(draft)이라도 match 가 의도대로 작동하는지 보려고 상태만 바꾼 사본을 쓴다.
    for path in NORM_DIR.glob("NR-*.md"):
        text = path.read_text(encoding="utf-8").replace("status: draft", "status: curated", 1)
        (tmp_path / path.name).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("result", "expected"), [
    (_result(category="택배"), ["NR-0015"]),
    (_result(brand="우체국택배", category="택배"), ["NR-0015", "NR-0011"]),
    (_result(brand="한진택배", category="택배"), ["NR-0015", "NR-0012"]),
    (_result(brand="롯데택배", category="택배"), ["NR-0015", "NR-0013"]),
    (_result(brand="로젠택배", category="택배"), ["NR-0015", "NR-0014"]),
    (_result(category="금융", doubts=["전화 응대"]), ["NR-0019"]),
    (_result(category="금융"), []),
    (_result(category="공공기관"), []),
])
def test_real_rules_match_as_designed(tmp_path, result, expected):
    assert [g.rule_id for g in find_guidance(result, _curated_copy(tmp_path))] == expected


def test_real_rules_return_nothing_until_reviewed():
    # 지금 자료는 모두 draft 다. 사용자 검토 후 curated 로 바뀌기 전에는 아무것도 나가지 않는다.
    drafts = all("status: draft" in p.read_text(encoding="utf-8") for p in NORM_DIR.glob("NR-*.md"))
    if not drafts:
        pytest.skip("검토를 마친 자료가 생겼다")
    assert find_guidance(_result(brand="우체국택배", category="택배")) == []
