"""#155: 임베딩이 늦어도 TF-IDF 결과는 검색 예산(2.5초) 안에 돌아온다."""

import asyncio
import logging
import time
from pathlib import Path

import pytest

import ai.kb.search as search
from ai.case.message import SEARCH_BUDGET_S
from ai.types import AnalysisStatus

LOAN = "저금리 대환대출 상품 안내드립니다 기존 대출 상환하시려면 선입금이 필요합니다"


def _record(case_id: str, text: str) -> str:
    return f"""---
id: {case_id}
status: curated
type: loan_fraud
variants:
  - "{text}"
categories: []
---
"""


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    (tmp_path / "VC-9001.md").write_text(_record("VC-9001", LOAN), encoding="utf-8")
    return tmp_path


@pytest.fixture
def slow_embedding(monkeypatch):
    """KB 벡터는 있고 질의 임베딩만 3초 걸린다(운영의 ReadTimeout 상황)."""

    monkeypatch.setattr(search.semantic, "load_index", lambda *args: object())
    monkeypatch.setattr(search.semantic, "scores", lambda index, query: {})

    def slow(text):
        time.sleep(3.0)
        return (1.0,)

    monkeypatch.setattr(search.semantic, "embed_query", slow)
    monkeypatch.setattr(search.semantic, "embed_transcript", slow)


@pytest.mark.parametrize("kind", ["text", "voice"])
def test_slow_embedding_returns_tfidf_within_budget(kb, slow_embedding, kind, caplog):
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        if kind == "text":
            result = search.search_cases(LOAN, cases_dir=kb, min_similarity=0.1)
        else:
            result = search.search_voice_cases(LOAN, cases_dir=kb, min_similarity=0.1)
    elapsed = time.perf_counter() - started

    assert elapsed < SEARCH_BUDGET_S
    assert result.status is AnalysisStatus.COMPLETED
    assert [m.case_id for m in result.matches] == ["VC-9001"]
    # 혼합 검색 실패와 TF-IDF 폴백을 로그로 구분한다. 문자 내용은 남기지 않는다
    assert "query embedding deadline" in caplog.text
    assert "case search mode=tfidf matches=1" in caplog.text
    assert LOAN not in caplog.text


async def test_slow_embedding_still_reaches_wait_for_caller(kb, slow_embedding):
    # 운영 호출부처럼 바깥 2.5초 wait_for 로 감싸도 결과가 버려지지 않는다
    result = await asyncio.wait_for(
        asyncio.to_thread(search.search_cases, LOAN, cases_dir=kb, min_similarity=0.1),
        timeout=SEARCH_BUDGET_S,
    )
    assert [m.case_id for m in result.matches] == ["VC-9001"]


def test_fast_embedding_is_logged_as_hybrid(kb, monkeypatch, caplog):
    monkeypatch.setattr(search.semantic, "load_index", lambda *args: object())
    monkeypatch.setattr(search.semantic, "scores", lambda index, query: {"VC-9001": 1.0})
    monkeypatch.setattr(search.semantic, "embed_query", lambda text: (1.0,))
    with caplog.at_level(logging.WARNING):
        result = search.search_cases(LOAN, cases_dir=kb, min_similarity=0.1)
    assert result.matches
    assert "case search mode=hybrid matches=1" in caplog.text


async def test_whole_budget_timeout_is_logged_and_marked(caplog):
    from ai.case.message import MessageRuntime, _search as message_search
    from ai.case.types import UncheckedReason

    def stuck(text):
        time.sleep(0.5)

    with caplog.at_level(logging.WARNING):
        found, miss = await message_search(MessageRuntime(search_cases=stuck), "본문", 0.1)
    # 검색 실패(시간 초과)는 "사례 없음" 과 다른 값으로 남는다
    assert found is None and miss is UncheckedReason.TIMEOUT
    assert "message case search timed out" in caplog.text
