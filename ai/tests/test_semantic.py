import re
from pathlib import Path

import pytest

import ai.case.message as case_message
import ai.case.voice as case_voice
import ai.pipeline.analysis as analysis
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


def test_query_budget_leaves_room_for_tfidf_fallback():
    assert semantic.QUERY_TIMEOUT_S < case_message.SEARCH_BUDGET_S
    assert semantic.QUERY_TIMEOUT_S < case_voice.SEARCH_BUDGET_S
    assert semantic.QUERY_TIMEOUT_S < analysis.SEARCH_TIMEOUT_SECONDS


def test_query_embedding_logs_elapsed_time_without_text(cases_dir, monkeypatch, caplog):
    index = semantic.EmbeddingIndex({"CE-9101": ((1.0, 0.0),), "CE-9102": ((0.0, 1.0),)})
    monkeypatch.setattr(semantic, "load_index", lambda *args: index)
    monkeypatch.setattr(semantic, "embed_query", lambda text: (0.0, 1.0))

    with caplog.at_level("WARNING", logger="ai.kb.search"):
        search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0)

    assert re.search(r"query embedding elapsed=\d+\.\d{3}s kind=text", caplog.text)
    assert "우체국" not in caplog.text


def test_query_embedding_failure_logs_cause_and_elapsed_time(cases_dir, monkeypatch, caplog):
    index = semantic.EmbeddingIndex({"CE-9101": ((1.0, 0.0),), "CE-9102": ((0.0, 1.0),)})
    monkeypatch.setattr(semantic, "load_index", lambda *args: index)

    def boom(text):
        raise TimeoutError

    monkeypatch.setattr(semantic, "embed_query", boom)

    with caplog.at_level("WARNING", logger="ai.kb.search"):
        search_cases("우체국택배 주소 확인 부탁합니다", cases_dir=cases_dir, min_similarity=0.0)

    assert re.search(r"query embedding failed: TimeoutError elapsed=\d+\.\d{3}s kind=text", caplog.text)
    assert "우체국" not in caplog.text


def _configured(monkeypatch):
    monkeypatch.setattr(semantic.embedding, "is_configured", lambda: True)


def test_index_retry_after_failure_runs_in_background(monkeypatch):
    """재시도는 요청 스레드에서 돌지 않는다. KB 전체 임베딩이 검색 예산을 넘기기 때문이다."""
    import threading

    _configured(monkeypatch)
    release = threading.Event()
    built = threading.Event()
    index = semantic.EmbeddingIndex({"A": ((1.0,),)})

    def fail(cases, kind):
        raise RuntimeError("down")

    def slow_build(cases, kind):
        release.wait(5)
        built.set()
        return index

    monkeypatch.setattr(semantic, "_build", fail)
    assert semantic.load_index(Path("kb"), "text", ["case"]) is None

    monkeypatch.setattr(semantic, "RETRY_AFTER_S", 0.0)
    monkeypatch.setattr(semantic, "_build", slow_build)

    # 재시도를 시작한 호출도, 만드는 중에 온 호출도 기다리지 않고 None 을 받는다
    assert semantic.load_index(Path("kb"), "text", ["case"]) is None
    assert semantic.load_index(Path("kb"), "text", ["case"]) is None
    assert not built.is_set()

    release.set()
    assert built.wait(5)
    for _ in range(100):
        if semantic.load_index(Path("kb"), "text", ["case"]) is index:
            break
        threading.Event().wait(0.01)
    assert semantic.load_index(Path("kb"), "text", ["case"]) is index


def test_first_index_build_happens_where_it_is_called(monkeypatch):
    _configured(monkeypatch)
    index = semantic.EmbeddingIndex({"A": ((1.0,),)})
    monkeypatch.setattr(semantic, "_build", lambda cases, kind: index)

    assert semantic.load_index(Path("kb"), "voice", ["case"]) is index
