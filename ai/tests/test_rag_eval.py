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

    monkeypatch.setattr(rag_eval, "load_rows", lambda: [{"text": "x"}])
    monkeypatch.setattr(rag_eval, "default_searcher", searcher)
    monkeypatch.setattr(rag_eval, "load_cases", lambda: calls.append("load") or ())

    with pytest.raises(Stop):
        rag_eval.main(["--label", "t"])

    assert calls == ["search"]
