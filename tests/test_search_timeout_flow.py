"""#155 BE 검증: 질의 임베딩이 늦어도 사례 검색 결과가 최종 분석과 카드에 반영된다.

외부 임베딩만 느리게 바꾸고 검색·분석·카드 조합 코드는 실제 것을 쓴다.
"""

import logging
import time
from pathlib import Path

import pytest

import ai.kb.search as search
import main
from ai.case import MessageRuntime, analyze_message_case
from ai.case.lexicon import read_with_lexicon
from ai.types import FailureCode

TEXT = "엄마 나 폰 고장나서 이 번호로 문자해. 문화상품권 사서 핀번호 보내줘"


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    (tmp_path / "CE-9001.md").write_text(f"""---
id: CE-9001
status: curated
variants:
  - "{TEXT}"
categories: []
---
""", encoding="utf-8")
    return tmp_path


@pytest.fixture
def slow_embedding(monkeypatch):
    monkeypatch.setattr(search.semantic, "load_index", lambda *args: object())
    monkeypatch.setattr(search.semantic, "scores", lambda index, query: {})

    def slow(text):
        time.sleep(3.0)
        return (1.0,)

    monkeypatch.setattr(search.semantic, "embed_query", slow)


async def _lexicon_read(source):
    return read_with_lexicon(source, FailureCode.TIMEOUT)


def _use_search(monkeypatch, search_cases):
    runtime = MessageRuntime(read_claims=_lexicon_read, search_cases=search_cases)

    async def analyze(text, **kwargs):
        kwargs["runtime"] = runtime
        return await analyze_message_case(text, **kwargs)

    monkeypatch.setattr(main, "analyze_message_case", analyze)


def _description(card: dict) -> str:
    return card["template"]["outputs"][0]["textCard"]["description"]


async def test_slow_embedding_still_reports_similar_cases(monkeypatch, kb, slow_embedding, caplog):
    _use_search(monkeypatch, lambda text: search.search_cases(text, cases_dir=kb, min_similarity=0.1))
    jobs = {"job-1": {"status": "running"}}
    monkeypatch.setattr(main, "ANALYSIS_JOBS", jobs)

    with caplog.at_level(logging.WARNING):
        card = await main.run_message_case(TEXT, job_id="job-1")

    payload = jobs["job-1"]["final_payload"]
    assert [m["case_id"] for m in payload["similar_cases"]["matches"]] == ["CE-9001"]
    assert "similar_cases" in payload["checked"]
    assert "- 비슷한 사례" in _description(card)
    assert "비슷한 사례 (" not in _description(card)
    # 진단 로그에 문자 내용이 남지 않는다
    assert "case search mode=tfidf matches=1" in caplog.text
    assert "핀번호" not in caplog.text


async def test_whole_budget_timeout_shows_unchecked_not_no_cases(monkeypatch, caplog):
    def stuck(text):
        time.sleep(3.0)

    _use_search(monkeypatch, stuck)

    with caplog.at_level(logging.WARNING):
        card = await main.run_message_case(TEXT)

    # 검색 실패(시간 초과)는 "사례 없음" 이 아니라 확인하지 못한 것으로 보인다
    assert "- 비슷한 사례 (시간 안에 끝내지 못했어요)" in _description(card)
    assert "message case search timed out" in caplog.text
    # 판정과 카드 종류는 그대로다
    assert card["template"]["outputs"][0]["textCard"]["title"] == "🔎 주의가 필요한 문자예요"
