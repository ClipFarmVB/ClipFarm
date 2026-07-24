"""Unit tests for the time-based evaluation metrics (ml/eval/metrics.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval.metrics import (
    ModelWindow,
    _decompose_window,
    evaluate,
    intersect_length,
    union,
)


def mw(start: float, end: float, score: float | None = None) -> ModelWindow:
    return ModelWindow(start, end, score)


class TestIntervalHelpers:
    def test_union_merges_and_sorts(self):
        assert union([(5, 6), (0, 3), (2, 4)]) == [(0.0, 4.0), (5.0, 6.0)]

    def test_union_drops_empty_intervals(self):
        assert union([(5, 5), (10, 8)]) == []

    def test_intersect_length_partial(self):
        assert intersect_length([(0, 10)], [(5, 20)]) == 5.0

    def test_intersect_length_disjoint(self):
        assert intersect_length([(0, 10)], [(20, 30)]) == 0.0

    def test_intersect_length_unions_first(self):
        # Overlapping inputs on the same side must not double-count.
        assert intersect_length([(0, 6), (4, 10)], [(0, 10)]) == 10.0


class TestExactMatch:
    def test_perfect(self):
        sig = evaluate([(10, 20)], [mw(10, 20, 0.9)])
        assert sig.captured_pct == 1.0
        assert (sig.buckets.well_captured, sig.buckets.butchered, sig.buckets.missed) == (1, 0, 0)
        assert sig.incorrect.total == 0.0
        assert sig.per_window[0].covered == 10.0


class TestPartialOverlaps:
    def test_model_inside_human_is_butchered_no_slop(self):
        # Model (2,5) sits fully inside human (0,10): under-covers, but adds nothing wrong.
        sig = evaluate([(0, 10)], [mw(2, 5)])
        assert sig.captured_pct == 0.3
        assert sig.buckets.butchered == 1
        assert sig.incorrect.total == 0.0

    def test_model_wraps_human_gives_lead_and_tail_slop(self):
        # Model (5,25) around human (10,20): 5s lead + 5s tail slop, fully captured.
        sig = evaluate([(10, 20)], [mw(5, 25)])
        assert sig.captured_pct == 1.0
        pw = sig.per_window[0]
        assert pw.lead_slop == 5.0
        assert pw.tail_slop == 5.0
        assert pw.bridge == 0.0
        assert pw.covered == 10.0

    def test_window_length_invariant(self):
        # covered + junk + lead + tail + bridge must equal the window length.
        window = mw(5, 25)
        pw = _decompose_window(window, union([(10, 20)]))
        assert pw.covered + pw.junk + pw.lead_slop + pw.tail_slop + pw.bridge == 20.0


class TestBridge:
    def test_one_window_spanning_two_human_clips(self):
        # Model (10,40) covers both human clips but bridges the 20-30 dead gap.
        sig = evaluate([(10, 20), (30, 40)], [mw(10, 40)])
        assert sig.captured_pct == 1.0
        assert sig.buckets.well_captured == 2
        pw = sig.per_window[0]
        assert pw.bridge == 10.0
        assert pw.lead_slop == 0.0
        assert pw.tail_slop == 0.0
        assert pw.covered == 20.0


class TestJunk:
    def test_pure_junk_window(self):
        sig = evaluate([(10, 20)], [mw(50, 60, 0.1)])
        assert sig.captured_pct == 0.0
        assert sig.buckets.missed == 1
        assert sig.incorrect.junk == 10.0
        assert sig.per_window[0].is_junk is True


class TestEmptyInputs:
    def test_empty_model(self):
        sig = evaluate([(10, 20)], [])
        assert sig.captured_pct == 0.0
        assert sig.buckets.missed == 1
        assert sig.incorrect.total == 0.0
        assert sig.auc is None
        assert sig.per_window == []

    def test_empty_human(self):
        sig = evaluate([], [mw(10, 20, 0.5)])
        assert sig.captured_pct is None          # no human seconds to divide by
        assert sig.buckets.total == 0
        assert sig.incorrect.junk == 10.0        # nothing to cover → all junk
        assert sig.auc is None                   # positives empty


class TestAUC:
    def test_clean_separation(self):
        sig = evaluate([(10, 20)], [mw(10, 20, 0.9), mw(50, 60, 0.1)])
        assert sig.auc == 1.0

    def test_ties_count_half(self):
        # pos scores [0.8]; neg scores [0.8, 0.2] → (0.5 + 1.0) / 2 = 0.75
        sig = evaluate([(10, 20)], [mw(10, 20, 0.8), mw(50, 60, 0.8), mw(70, 80, 0.2)])
        assert sig.auc == 0.75

    def test_missing_scores_yield_none(self):
        sig = evaluate([(10, 20)], [mw(10, 20), mw(50, 60)])
        assert sig.auc is None


class TestBucketBoundary:
    def test_exactly_half_counts_as_well_captured(self):
        # f == 0.5 falls in the well-captured bucket (>= 0.5), not butchered.
        sig = evaluate([(0, 10)], [mw(0, 5)])
        assert sig.per_clip[0].fraction == 0.5
        assert sig.buckets.well_captured == 1
        assert sig.buckets.butchered == 0
