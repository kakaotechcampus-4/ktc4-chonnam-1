from pathlib import Path

import pytest

import ai.kb.search as search
from ai.kb.search import (
    CASES_DIR,
    NGRAM_SIZES,
    SUBLINEAR_TF,
    VOICE_CASES_DIR,
    VOICE_MIN_SIMILARITY,
    VOICE_NGRAM_SIZES,
    VOICE_SUBLINEAR_TF,
    load_index,
    search_voice_cases,
)
from ai.types import AnalysisStatus


def _record(case_id: str, text: str, status: str = "curated") -> str:
    return f"""---
id: {case_id}
status: {status}
origin: synthetic
type: loan_fraud
group: g-{case_id}
reviewer: auto-import-2026-10-28
normalized: ""
variants:
  - "{text}"
categories: []
---

테스트 통화.
"""


LOAN = "저금리 대환대출 상품 안내드립니다 기존 대출 상환하시려면 선입금이 필요합니다"
POLICE = "서울중앙지검 수사관입니다 명의 도용 사건으로 연락드렸습니다 녹취 진행하겠습니다"
FAMILY = "엄마 나 폰 액정 깨져서 친구 폰으로 연락해 상품권 좀 사서 보내줘"


@pytest.fixture
def voice_dir(tmp_path: Path) -> Path:
    (tmp_path / "VC-9001.md").write_text(_record("VC-9001", LOAN), encoding="utf-8")
    (tmp_path / "VC-9002.md").write_text(_record("VC-9002", POLICE), encoding="utf-8")
    (tmp_path / "VC-9003.md").write_text(_record("VC-9003", FAMILY), encoding="utf-8")
    (tmp_path / "VC-9004.md").write_text(_record("VC-9004", LOAN + " 초안", "draft"), encoding="utf-8")
    (tmp_path / "README.md").write_text("# voice KB\n", encoding="utf-8")
    return tmp_path


def test_voice_search_finds_same_call(voice_dir):
    result = search_voice_cases(LOAN, cases_dir=voice_dir)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.matches[0].case_id == "VC-9001"
    assert result.matches[0].similarity == pytest.approx(1.0)
    # 통화 유형은 문자 주제(CategoryCode)가 아니다. 유형은 KB 레코드의 type 에 있다.
    assert result.matches[0].categories == []


def test_voice_search_skips_draft_record(voice_dir):
    result = search_voice_cases(LOAN + " 초안", cases_dir=voice_dir)

    assert "VC-9004" not in [match.case_id for match in result.matches]


def test_voice_search_defaults_to_voice_threshold_and_top3(voice_dir, monkeypatch):
    monkeypatch.setattr(search, "VOICE_MIN_SIMILARITY", 0.99)
    assert [m.case_id for m in search_voice_cases(LOAN, cases_dir=voice_dir).matches] == ["VC-9001"]

    everything = search_voice_cases(LOAN, cases_dir=voice_dir, min_similarity=0.0)
    assert len(everything.matches) == 3
    assert len(search_voice_cases(LOAN, cases_dir=voice_dir, min_similarity=0.0, top_k=1).matches) == 1


def test_voice_search_returns_fallback_for_blank_text_or_empty_kb(voice_dir, tmp_path):
    assert search_voice_cases("  ", cases_dir=voice_dir).status is AnalysisStatus.FALLBACK
    empty = tmp_path / "empty"
    empty.mkdir()
    result = search_voice_cases(LOAN, cases_dir=empty)
    assert result.status is AnalysisStatus.FALLBACK
    assert result.matches == []


def test_voice_index_uses_voice_parameters(voice_dir):
    index = load_index(voice_dir, ngram_sizes=VOICE_NGRAM_SIZES, sublinear_tf=VOICE_SUBLINEAR_TF)

    assert index.ngram_sizes == VOICE_NGRAM_SIZES
    assert index.sublinear_tf is VOICE_SUBLINEAR_TF


def test_smishing_defaults_stay_unchanged():
    # 스미싱 검색 값은 rag_eval.py 로 정했다. voice 값을 더해도 바뀌면 안 된다.
    assert (NGRAM_SIZES, SUBLINEAR_TF, search.DEFAULT_MIN_SIMILARITY) == ((3,), False, 0.2345)
    assert VOICE_CASES_DIR != CASES_DIR
    assert 0.0 < VOICE_MIN_SIMILARITY < 1.0


def test_voice_search_reads_voice_kb_by_default():
    result = search_voice_cases(POLICE, min_similarity=0.0)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.matches
    assert all(match.case_id.startswith("VC-") for match in result.matches)
