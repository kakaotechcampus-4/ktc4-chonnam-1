from pathlib import Path

import pytest

from ai.kb import search, semantic
from ai.kb.search import search_cases
from ai.types import AnalysisStatus, CaseMatch

CASE = """---
id: CE-9101
status: curated
origin: team_collected
collected_at: 2026-09-18
reviewer: tester
variants:
  - "{text}"
categories: [{category}]
---
"""


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    (tmp_path / "CE-9101.md").write_text(CASE.format(text="우체국택배 주소 확인 부탁합니다", category="delivery"), encoding="utf-8")
    (tmp_path / "CE-9102.md").write_text(
        CASE.format(text="엄마 나 폰 고장나서 이 번호로 연락해", category="acquaintance_impersonation").replace("9101", "9102"),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _reset():
    semantic.reset()
    yield
    semantic.reset()


def test_fuse_mixes_scores_and_reorders():
    matches = [
        CaseMatch(case_id="A", similarity=0.5, matched_variant="a", categories=[]),
        CaseMatch(case_id="B", similarity=0.1, matched_variant="b", categories=[]),
    ]

    fused = search.fuse(matches, {"A": 0.2, "B": 0.9}, alpha=0.3)

    assert [m.case_id for m in fused] == ["B", "A"]
    assert fused[0].similarity == round(0.3 * 0.1 + 0.7 * 0.9, 4)


def test_without_embedding_config_search_is_tfidf_only(cases_dir, monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)

    result = search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.matches[0].case_id == "CE-9101"


def test_query_embedding_failure_falls_back_to_tfidf(cases_dir, monkeypatch):
    index = semantic.EmbeddingIndex({"CE-9101": ((1.0, 0.0),), "CE-9102": ((0.0, 1.0),)})
    monkeypatch.setattr(semantic, "load_index", lambda *args: index)

    def boom(text):
        raise TimeoutError

    monkeypatch.setattr(semantic, "embed_query", boom)

    tfidf = search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0, use_embedding=False)
    fallback = search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0)

    assert fallback.matches == tfidf.matches


def test_embedding_scores_change_ranking_and_threshold(cases_dir, monkeypatch):
    index = semantic.EmbeddingIndex({"CE-9101": ((1.0, 0.0),), "CE-9102": ((0.0, 1.0),)})
    monkeypatch.setattr(semantic, "load_index", lambda *args: index)
    monkeypatch.setattr(semantic, "embed_query", lambda text: (0.0, 1.0))

    # 글자는 택배 사례와 겹치지만 임베딩은 지인 사칭 사례와 같다
    result = search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0)
    gated = search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir)

    assert result.matches[0].case_id == "CE-9102"
    assert all(m.similarity >= search.HYBRID_MIN_SIMILARITY for m in gated.matches)


def test_chunk_keeps_words_and_overlaps():
    pieces = semantic.chunk("가나 다라 마바 사아 자차 카타", 6)

    assert pieces[0] == "가나 다라"
    assert all(" ".join(p.split()) == p for p in pieces)
    assert len(pieces) > 1
