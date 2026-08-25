"""Unit tests for the shared interval primitives (ml/pipeline/intervals.py).

Split out of test_dead_time.py by CF-95. They import from ml.pipeline.intervals
directly rather than through dead_time, which still re-exports the names for its
own use — a test that reached through the re-export would pass even if the move
had not happened.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.intervals import merge_intervals


class TestMergeIntervals:
    def test_empty(self):
        assert merge_intervals([]) == []

    def test_disjoint_stay_separate(self):
        assert merge_intervals([(0, 1), (5, 6)], merge_gap_seconds=1.0) == [(0, 1), (5, 6)]

    def test_within_gap_merge(self):
        assert merge_intervals([(0, 1), (2, 3)], merge_gap_seconds=1.5) == [(0, 3)]

    def test_overlapping_merge(self):
        assert merge_intervals([(0, 5), (3, 8)]) == [(0, 8)]

    def test_contained_interval_absorbed(self):
        assert merge_intervals([(0, 10), (2, 4)]) == [(0, 10)]

    def test_unsorted_input(self):
        assert merge_intervals([(5, 6), (0, 1)], merge_gap_seconds=0.5) == [(0, 1), (5, 6)]

