import sys
from pathlib import Path

import pytest

# 평가 코드는 런타임 패키지 밖(ai/eval)에 있어 경로로 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import rag_metrics as metrics  # noqa: E402
from ai.types import CaseMatch, CategoryCode  # noqa: E402


def _match(score, category="delivery"):
    return CaseMatch(
        case_id=f"CE-{score}",
        similarity=score,
        matched_variant="사례",
        categories=[CategoryCode(category)],
    )


def test_rows_from_one_template_land_in_the_same_half():
    rows = [{"id": f"B-delivery-{i:02d}", "template": "B-delivery-t1"} for i in range(1, 6)]

    assert len({metrics.is_dev(row) for row in rows}) == 1


def test_rows_without_template_split_by_id():
    assert metrics.split_key({"id": "S-penalty-01", "template": None}) == "S-penalty-01"
    assert metrics.split_key({"id": "B-penalty-01", "template": "B-penalty-t2"}) == "B-penalty-t2"


def test_split_is_roughly_half_and_stable():
    rows = [{"id": f"S-x-{i:03d}", "template": None} for i in range(200)]
    first = [metrics.is_dev(row) for row in rows]

    assert first == [metrics.is_dev(row) for row in rows]
    assert 70 <= sum(first) <= 130


def test_first_hit_rank_skips_wrong_type_and_stops_below_threshold():
    matches = [_match(0.5, "payment"), _match(0.4, "delivery"), _match(0.2, "delivery")]

    assert metrics.first_hit_rank(matches, "delivery", 0.3) == 2
    assert metrics.first_hit_rank(matches, "delivery", 0.45) is None
    assert metrics.first_hit_rank(matches, "delivery", 0.3, k=1) is None
    assert metrics.first_hit_rank([], "delivery", 0.0) is None


def test_hit_rate_and_mrr():
    ranks = [1, 3, None, 2]

    assert metrics.hit_rate(ranks, 1) == 0.25
    assert metrics.hit_rate(ranks, 3) == 0.75
    assert metrics.mrr(ranks) == pytest.approx((1 + 1 / 3 + 1 / 2) / 4)


def test_attach_is_inclusive_and_needs_a_match():
    assert metrics.attached([_match(0.3)], 0.3)
    assert not metrics.attached([_match(0.2999)], 0.3)
    assert not metrics.attached([], 0.0)
    assert metrics.attach_rate([[_match(0.3)], [_match(0.1)], []], 0.3) == pytest.approx(1 / 3)


def test_auc_counts_ties_as_half():
    assert metrics.auc([0.9, 0.5], [0.5, 0.1]) == 0.875


def test_pick_threshold_takes_lowest_candidate_that_meets_the_rate():
    hard_negatives = [[_match(score)] for score in (0.1, 0.2, 0.3, 0.4)]

    assert metrics.pick_threshold(hard_negatives, 0.25, [0.05, 0.15, 0.35, 0.4]) == 0.35


def test_pick_threshold_goes_above_tied_maximum_when_no_candidate_fits():
    hard_negatives = [[_match(0.5)], [_match(0.5)]]

    assert metrics.pick_threshold(hard_negatives, 0.0, [0.1, 0.5]) == 0.5001
