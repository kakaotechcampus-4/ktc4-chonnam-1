import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import rag_eval  # noqa: E402
from ai.types import CaseMatch, CategoryCode  # noqa: E402


def _match(score, category="delivery"):
    return CaseMatch(
        case_id=f"CE-{score}",
        similarity=score,
        matched_variant=f"사례 {score}",
        categories=[CategoryCode(category)],
    )


def _result(row_id, group, category, score, matched_category="delivery", text=None):
    row = {
        "id": row_id,
        "group": group,
        "category": category,
        "text": text or f"{row_id} 본문",
        "message": text or f"{row_id} 본문",
        "template": None,
    }
    return rag_eval.RowResult(row, [_match(score, matched_category)], 0.0)


# dev: hard negative 0.2·0.4 → max_rate 0.5 에서 기준값 0.4
RESULTS = [
    _result("dev-s1", "smishing", "delivery", 0.6),
    _result("dev-h1", "hard_negative", "delivery", 0.2),
    _result("dev-h2", "hard_negative", "delivery", 0.4),
    _result("dev-b1", "benign", "delivery", 0.1),
    _result("test-s1", "smishing", "delivery", 0.5),
    _result("test-s2", "smishing", "penalty", 0.45),
    _result("test-h1", "hard_negative", "penalty", 0.42),
    _result("test-b1", "benign", "delivery", 0.05),
]


def _is_dev(row):
    return row["id"].startswith("dev-")


def _evaluate(results=RESULTS, kb_counts=None, **kwargs):
    kb_counts = {"delivery": 3} if kb_counts is None else kb_counts
    return rag_eval.evaluate(results, kb_counts, max_rate=0.5, is_dev=_is_dev, **kwargs)


def test_threshold_is_picked_on_dev_and_applied_to_test():
    evaluation = _evaluate()

    assert evaluation["threshold"] == 0.4
    test = evaluation["test"]
    assert test["smishing"]["n"] == 2
    assert test["smishing"]["hit3"] == 0.5
    assert test["hard_negative"]["attach"] == 1.0
    assert test["benign"]["attach"] == 0.0
    assert test["hard_negative"]["auc"] == 1.0


def test_failures_list_test_rows_only():
    evaluation = _evaluate()

    assert [result.row["id"] for result in evaluation["misses"]] == ["test-s2"]
    assert [result.row["id"] for result in evaluation["attached"]] == ["test-h1"]


def test_dev_only_leaves_test_unseen():
    evaluation = _evaluate(include_test=False)

    assert "test" not in evaluation
    assert "categories" not in evaluation
    assert evaluation["dev"]["smishing"]["n"] == 1


def test_category_table_counts_rows_and_keeps_kb_only_categories():
    evaluation = _evaluate(kb_counts={"delivery": 3, "obituary": 2})
    table = {row["category"]: row for row in evaluation["categories"]}

    assert table["delivery"]["smishing"] == (1, 1)
    assert table["delivery"]["benign"] == (0, 1)
    assert table["penalty"]["hard_negative"] == (1, 1)
    assert table["obituary"] == {
        "category": "obituary",
        "kb": 2,
        "smishing": (0, 0),
        "benign": (0, 0),
        "hard_negative": (0, 0),
    }


def test_report_flags_unlabelled_kb_and_escapes_cells():
    odd = _result("test-b2", "benign", "delivery", 0.9, text="줄\n바꿈 | 파이프")
    evaluation = _evaluate(results=[*RESULTS, odd], kb_counts={})
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(evaluation, meta)

    assert rag_eval.SYNTHETIC_WARNING in report
    assert "측정 불가" in report
    assert "기준값: 0.4000" in report
    assert "줄 바꿈 \\| 파이프" in report


def test_report_shows_hit3_interval():
    evaluation = _evaluate()
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(evaluation, meta)

    assert evaluation["test"]["smishing"]["hits3"] == 1
    assert "test 스미싱 hit@3: 50.0% (1/2, 95% 구간 9.5%~90.5%)" in report


def test_verdict_follows_the_interval_not_the_point():
    # 1/2 는 점 추정이 목표 50% 와 같지만 구간이 9.5%~90.5% 라 판단할 수 없다.
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(_evaluate(), meta)

    assert "잠정 목표 50.0% 판단 보류" in report


def test_search_uses_message_not_source_text():
    # 운영에서 AI 는 BE 가 주소를 지운 본문만 받는다. 출처 원문(text)으로 재면 운영과 조건이 다르다.
    queries = []
    rows = [{"text": "배송 확인 https://a.example/x", "message": "배송 확인"}]

    rag_eval.run_search(rows, lambda text: queries.append(text) or [])

    assert queries == ["배송 확인"]


def test_report_shows_query_rule_and_failed_message():
    miss = _result("test-s3", "smishing", "penalty", 0.1, text="원문")
    miss.row["message"] = "질의 본문"
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(_evaluate(results=[*RESULTS, miss]), meta)

    assert rag_eval.QUERY_NOTE in report
    assert "| test-s3 | 0.1000 | 질의 본문 |" in report


def _known(row_id, category, score, matched_category="delivery"):
    result = _result(row_id, "smishing", category, score, matched_category)
    result.row["near_dup_of"] = "CE-1"
    return result


def test_campaign_split_separates_known_and_new_smishing():
    # near_dup_of 는 KB 에 거의 같은 사례가 있다는 사람 판정이다. 정상 문자는 나누지 않는다.
    results = [
        _known("test-k1", "delivery", 0.9),
        _known("test-k2", "penalty", 0.8),
        _result("test-n1", "smishing", "delivery", 0.5),
        _result("test-h9", "hard_negative", "delivery", 0.9),
    ]

    split = rag_eval.campaign_split(results, threshold=0.4)

    assert (split["known"]["n"], split["known"]["hits3"]) == (2, 1)
    assert (split["new"]["n"], split["new"]["hits3"]) == (1, 1)


def test_campaign_split_handles_empty_side():
    split = rag_eval.campaign_split([_result("test-n1", "smishing", "delivery", 0.5)], threshold=0.4)

    assert split["known"] == {"n": 0}


def test_report_shows_known_and_new_campaign_hit3():
    results = [*RESULTS, _known("test-k1", "delivery", 0.9), _known("test-k2", "penalty", 0.8)]
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
    }

    report = rag_eval.render_report(_evaluate(results=results), meta)

    # test 스미싱: 아는 캠페인 k1(hit)·k2(miss), 새 캠페인 s1(hit)·s2(miss)
    assert "  - 아는 캠페인: 50.0% (1/2," in report
    assert "  - 새 캠페인: 50.0% (1/2," in report
    assert "## 스미싱: 아는 캠페인 / 새 캠페인" in report


def test_rows_marked_exclude_are_left_out():
    rows = [{"id": "a"}, {"id": "b", "exclude": "주소 판단 보류"}]

    assert rag_eval.drop_excluded(rows) == [{"id": "a"}]


def test_report_counts_excluded_rows():
    meta = {
        "label": "t",
        "date": "2026-10-03",
        "commit": "abc1234",
        "kb_total": 3,
        "settings": "s",
        "latency": {"first_ms": 1.0, "p50_ms": 0.5, "p95_ms": 0.9},
        "excluded": 5,
    }

    report = rag_eval.render_report(_evaluate(), meta)

    assert "평가 제외: 5행" in report


def test_parse_ngram_sorts_and_dedupes():
    assert rag_eval.parse_ngram("3,2,3") == (2, 3)


def test_make_searcher_uses_given_settings():
    from ai.kb.normalize import normalize
    from ai.kb.search import Case

    cases = [
        Case(
            case_id=f"CE-{i}",
            variants=(text,),
            normalized=(normalize(text),),
            categories=(CategoryCode.DELIVERY,),
        )
        for i, text in enumerate(["부고 안내", "택배 확인", "과태료 납부", "청첩장 도착"])
    ]

    matches = rag_eval.make_searcher(cases, (3,), True)("부고 안내")

    assert len(matches) == rag_eval.TOP_K
    assert matches[0].case_id == "CE-0"
    assert matches[0].similarity == 1.0


def test_default_run_times_kb_load_inside_first_search(monkeypatch):
    # 리포트의 첫 호출 지연에는 KB 로드가 들어가야 한다. load_cases 를 먼저 부르면
    # 캐시가 차서 첫 검색 시간에서 빠진다.
    import pytest

    class Stop(Exception):
        pass

    calls = []

    def searcher(text):
        calls.append("search")
        raise Stop

    monkeypatch.setattr(rag_eval, "load_rows", lambda: [{"message": "x"}])
    monkeypatch.setattr(rag_eval, "default_searcher", searcher)
    monkeypatch.setattr(rag_eval, "load_cases", lambda: calls.append("load") or ())

    with pytest.raises(Stop):
        rag_eval.main(["--label", "t"])

    assert calls == ["search"]
