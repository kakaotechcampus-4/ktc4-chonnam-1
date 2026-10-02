from pathlib import Path

import pytest

from ai.kb.normalize import normalize
from ai.kb.search import Case, build_index, load_cases, rank, search_cases
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
    # 낮은 점수로 잡히는 것은 정상이다 — 검증 대상은 draft 제외뿐이다.
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


def test_concurrent_first_calls_parse_kb_once(cases_dir, monkeypatch):
    # Five cold requests used to parse the KB in parallel and all missed the
    # 1s search budget under the GIL.
    import threading

    import ai.kb.search as module

    started, release, parsed = threading.Event(), threading.Event(), []
    real_parse = module._parse_case

    def parse(path):
        parsed.append(path)
        if not started.is_set():
            started.set()
            release.wait(timeout=1)
        return real_parse(path)

    monkeypatch.setattr(module, "_parse_case", parse)
    results = []
    first = threading.Thread(target=lambda: results.append(load_cases(cases_dir)))
    first.start()
    started.wait(timeout=1)
    others = [threading.Thread(target=lambda: results.append(load_cases(cases_dir))) for _ in range(4)]
    for thread in others:
        thread.start()
    for thread in others:
        thread.join(timeout=0.05)
    release.set()
    for thread in [first, *others]:
        thread.join(timeout=1)

    assert len(parsed) == 3
    assert len(results) == 5 and all(result is results[0] for result in results)


def test_default_and_explicit_kb_path_share_one_cache():
    from ai.kb.search import CASES_DIR

    assert load_cases() is load_cases(CASES_DIR)


def _case(case_id, *variants):
    return Case(
        case_id=case_id,
        variants=variants,
        normalized=tuple(normalize(variant) for variant in variants),
        categories=(CategoryCode.DELIVERY,),
    )


def test_ngram_in_every_case_weighs_less_than_ngram_in_one():
    index = build_index(
        (_case("A", "고객님 택배 확인"), _case("B", "고객님 부고 확인"), _case("C", "고객님 과태료 확인"))
    )

    assert index.idf["고객"] < index.idf["부고"]


def test_query_text_missing_from_kb_lowers_the_score():
    # 설계: KB 에 없는 n-gram 도 질의 벡터 크기에 넣는다. 빼면 두 점수가 같아진다.
    index = build_index((_case("A", "부고 안내"),))

    exact = rank(index, "부고 안내")[0].similarity
    padded = rank(index, "부고 안내 오늘 회의 자료")[0].similarity

    assert exact == pytest.approx(1.0)
    assert 0.0 < padded < exact


def test_equal_scores_keep_kb_order():
    index = build_index((_case("A", "택배 확인"), _case("B", "택배 확인")))

    assert [match.case_id for match in rank(index, "택배 확인")] == ["A", "B"]


def test_query_without_searchable_characters_scores_zero(cases_dir):
    # normalize 가 기호를 모두 지우면 질의 벡터가 빈다. 0 으로 나누지 않아야 한다.
    result = search_cases("!!! ???", cases_dir=cases_dir, min_similarity=0.0)

    assert result.status is AnalysisStatus.COMPLETED
    assert [match.similarity for match in result.matches] == [0.0]


def test_threshold_equal_to_reported_similarity_keeps_the_match(cases_dir):
    # 평가 도구는 보고된(반올림된) 유사도로 기준값을 고른다. 운영 검색도 같은 값과
    # 비교해야 평가와 운영이 같은 사례를 남긴다.
    text = "우체국 택배 확인 부탁"
    top = search_cases(text, cases_dir=cases_dir, min_similarity=0.0).matches[0]

    kept = search_cases(text, cases_dir=cases_dir, min_similarity=top.similarity).matches

    assert [match.case_id for match in kept] == [top.case_id]
