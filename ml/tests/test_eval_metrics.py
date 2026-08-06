"""Unit tests for the time-based evaluation metrics (ml/eval/metrics.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval.metrics import (
    ModelWindow,
    _decompose_window,
    evaluate,
    evaluate_deadtime,
    intersect,
    intersect_length,
    subtract,
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


# ── dead-time metrics (CF-98) ──────────────────────────────────────────────

class TestSubtract:
    def test_removes_middle(self):
        assert subtract([(0, 100)], [(30, 40)]) == [(0, 30), (40, 100)]

    def test_disjoint_leaves_a_whole(self):
        assert subtract([(0, 10)], [(50, 60)]) == [(0, 10)]

    def test_full_cover_leaves_nothing(self):
        assert subtract([(10, 20)], [(0, 100)]) == []

    def test_complement_via_span(self):
        # dead time = the span minus the keep-windows
        assert subtract([(0, 100)], [(10, 30), (60, 80)]) == [(0, 10), (30, 60), (80, 100)]


class TestIntersect:
    def test_returns_overlap_spans(self):
        assert intersect([(0, 50)], [(20, 80)]) == [(20, 50)]

    def test_multiple_pieces(self):
        assert intersect([(0, 100)], [(10, 20), (40, 60)]) == [(10, 20), (40, 60)]

    def test_disjoint_is_empty(self):
        assert intersect([(0, 10)], [(20, 30)]) == []


class TestDeadTime:
    def test_perfect_condense(self):
        # model keeps exactly the rallies → all dead removed, no play lost
        keep = [(10, 20), (60, 80)]
        s = evaluate_deadtime(human_keep=keep, model_keep=keep, duration=100)
        assert s.dead_removed_pct == 1.0
        assert s.live_removed_sec == 0.0
        assert s.live_removed_pct == 0.0
        assert s.kept_play_pct == 1.0
        assert s.missed_dead == []
        assert s.over_cut_live == []

    def test_kept_all_dead_removed_nothing(self):
        # model keeps the whole video → 0% dead removed, but 0 play lost either
        s = evaluate_deadtime(human_keep=[(10, 20)], model_keep=[(0, 100)], duration=100)
        assert s.dead_removed_pct == 0.0
        assert s.live_removed_sec == 0.0
        # all dead time kept, listed longest-first
        assert s.missed_dead == [(20, 100), (0, 10)]

    def test_over_cut_live_is_the_harm(self):
        # human rally 10-30; model keeps only 10-20 → cut 20-30 of real play
        s = evaluate_deadtime(human_keep=[(10, 30)], model_keep=[(10, 20)], duration=100)
        assert s.live_removed_sec == 10.0
        assert s.live_removed_pct == 0.5
        assert s.kept_play_pct == 0.5
        assert s.over_cut_live == [(20, 30)]

    def test_audit_sorted_longest_first(self):
        # two over-cut spans of 5s and 20s → longer one listed first
        s = evaluate_deadtime(
            human_keep=[(10, 15), (40, 60)], model_keep=[], duration=100
        )
        assert s.over_cut_live == [(40, 60), (10, 15)]

    def test_inputs_clamped_to_duration(self):
        # a label past the video end must not inflate the totals
        s = evaluate_deadtime(human_keep=[(90, 200)], model_keep=[(90, 200)], duration=100)
        assert s.human_keep_sec == 10.0        # 90-100, not 90-200
        assert s.kept_play_pct == 1.0

    def test_no_dead_time(self):
        # whole video is rally → dead_removed_pct undefined, not a divide-by-zero
        s = evaluate_deadtime(human_keep=[(0, 100)], model_keep=[(0, 100)], duration=100)
        assert s.dead_removed_pct is None
        assert s.human_dead_sec == 0.0

    def test_condense_ratio(self):
        s = evaluate_deadtime(human_keep=[(0, 40)], model_keep=[(0, 40)], duration=100)
        assert s.condense_ratio == 0.4
