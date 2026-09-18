from pathlib import Path

import pytest

from ai.kb.search import load_cases, search_cases
from ai.types import AnalysisStatus, CategoryCode

CURATED = """---
id: CE-9001
status: curated
origin: team_collected
collected_at: 2026-09-18
reviewer: tester
normalized: "우체국택배확인부탁합니다"
variants:
  - "우체국택배 확인부탁합니다"
  - "우-체-국-택-배 확-인-부-탁-합-니-다"
categories: [delivery]
claimed_brand: 우체국택배
---

수집 사례.
"""

DRAFT = """---
id: CE-9002
status: draft
origin: team_collected
collected_at: 2026-09-18
reviewer: tester
normalized: "한진택배확인부탁합니다"
variants:
  - "한진택배 확인부탁합니다"
categories: [delivery]
claimed_brand: 한진택배
---

미검토 사례.
"""


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    (tmp_path / "CE-9001.md").write_text(CURATED, encoding="utf-8")
    (tmp_path / "CE-9002.md").write_text(DRAFT, encoding="utf-8")
    (tmp_path / "template.md").write_text("# 템플릿\n", encoding="utf-8")
    return tmp_path


def test_load_cases_indexes_only_curated(cases_dir):
    cases = load_cases(cases_dir)

    assert [case.case_id for case in cases] == ["CE-9001"]


def test_search_matches_obfuscated_variant(cases_dir):
    result = search_cases("우-체-국-택-배 확-인-부-탁-합-니-다", cases_dir=cases_dir)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.matches[0].case_id == "CE-9001"
    assert result.matches[0].similarity == pytest.approx(1.0)
    assert result.matches[0].categories == [CategoryCode.DELIVERY]


def test_search_ignores_draft_case(cases_dir):
    # CE-9002(draft)와 문면이 거의 같은 질의다. 인덱싱됐다면 유사도 1.0으로
    # 1위에 올라온다. 대신 curated인 CE-9001이 "확인부탁합니다" 어미를 공유해
    # 0.58로 잡히는 것은 정상이다 — 검증 대상은 draft 제외뿐이다.
    result = search_cases("한진택배 확인부탁합니다", cases_dir=cases_dir)

    assert "CE-9002" not in [match.case_id for match in result.matches]


def test_search_returns_fallback_for_blank_text(cases_dir):
    result = search_cases("   ", cases_dir=cases_dir)

    assert result.status is AnalysisStatus.FALLBACK
    assert result.matches == []


def test_search_returns_fallback_when_kb_empty(tmp_path):
    result = search_cases("우체국택배 확인부탁합니다", cases_dir=tmp_path)

    assert result.status is AnalysisStatus.FALLBACK
    assert result.matches == []


def test_search_drops_matches_below_threshold(cases_dir):
    result = search_cases("오늘 회의 자료 공유드립니다", cases_dir=cases_dir)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.matches == []


def test_search_respects_top_k(cases_dir):
    result = search_cases(
        "우체국택배 확인부탁합니다", cases_dir=cases_dir, top_k=1
    )

    assert len(result.matches) == 1
