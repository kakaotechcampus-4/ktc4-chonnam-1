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


def test_is_dev_reads_the_split_field():
    row = {"id": "S-x-01", "template": None, "source": "https://a"}

    assert metrics.is_dev({**row, "split": "dev"})
    assert not metrics.is_dev({**row, "split": "test"})


def test_split_key_prefers_template_then_source_then_id():
    assert metrics.split_key({"id": "B-penalty-01", "template": "B-penalty-t2", "source": None}) == "B-penalty-t2"
    assert metrics.split_key({"id": "S-penalty-01", "template": None, "source": "https://a"}) == "https://a"
    assert metrics.split_key({"id": "S-penalty-02", "template": None, "source": None}) == "S-penalty-02"


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


def test_wilson_interval():
    low, high = metrics.wilson(6, 31)

    assert (round(low, 4), round(high, 4)) == (0.0919, 0.3628)
    assert metrics.wilson(0, 10)[0] == pytest.approx(0.0, abs=1e-12)
    assert metrics.wilson(31, 31)[1] == pytest.approx(1.0, abs=1e-12)
    assert metrics.wilson(0, 0) == (0.0, 0.0)
