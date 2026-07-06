"""Unit tests for keep-window derivation (ml/pipeline/dead_time.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.dead_time import (
    active_windows_from_contacts,
    active_windows_from_detections,
    merge_intervals,
)


def contacts_at(*times: float) -> list[dict]:
    return [{"time": t} for t in times]


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


class TestActiveWindowsFromContacts:
    def test_empty_contacts(self):
        assert active_windows_from_contacts([], 100.0) == []

    def test_single_rally(self):
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 14), 100.0,
            pad_before=2.0, pad_after=2.5,
        )
        assert windows == [(8.0, 16.5)]

    def test_splits_on_large_gap(self):
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 30, 32), 100.0,
            gap_seconds=5.0, pad_before=2.0, pad_after=2.5,
        )
        assert windows == [(8.0, 14.5), (28.0, 34.5)]

    def test_min_contacts_drops_isolated_contact(self):
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 50), 100.0, min_contacts=2,
        )
        assert len(windows) == 1
        assert windows[0][0] == 8.0

    def test_padding_clamps_at_bounds(self):
        windows = active_windows_from_contacts(
            contacts_at(0.5, 1.0, 99.0, 99.5), 100.0,
            gap_seconds=5.0, pad_before=2.0, pad_after=2.5,
        )
        assert windows[0][0] == 0.0
        assert windows[-1][1] == 100.0

    def test_close_windows_merge(self):
        # Padded windows end at 14.5 and start at 15.0 — gap 0.5 < merge 1.5.
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 17, 19), 100.0,
            gap_seconds=4.0, pad_before=2.0, pad_after=2.5,
            merge_gap_seconds=1.5,
        )
        assert windows == [(8.0, 21.5)]

    def test_long_rally_stays_one_window(self):
        # 90s of continuous contacts — no MAX_CLIP_DURATION subdivision here.
        windows = active_windows_from_contacts(
            contacts_at(*range(10, 101, 2)), 200.0,
            gap_seconds=5.0, pad_before=2.0, pad_after=2.5,
        )
        assert len(windows) == 1
        assert windows[0] == (8.0, 102.5)

    def test_unsorted_contacts(self):
        windows = active_windows_from_contacts(
            contacts_at(14, 10, 12), 100.0, pad_before=2.0, pad_after=2.5,
        )
        assert windows == [(8.0, 16.5)]


class TestActiveWindowsFromDetections:
    def test_empty(self):
        assert active_windows_from_detections([], 100.0) == []

    def test_pads_and_clamps(self):
        windows = active_windows_from_detections(
            [{"start": 1.0, "end": 98.5}], 100.0,
            pad_before=2.0, pad_after=2.5,
        )
        assert windows == [(0.0, 100.0)]

    def test_merges_adjacent_rallies(self):
        windows = active_windows_from_detections(
            [{"start": 10.0, "end": 15.0}, {"start": 20.0, "end": 25.0}],
            100.0, pad_before=2.0, pad_after=2.5, merge_gap_seconds=1.5,
        )
        # Padded: (8, 17.5) and (18, 27.5) — gap 0.5 merges.
        assert windows == [(8.0, 27.5)]

    def test_disjoint_rallies_stay_split(self):
        windows = active_windows_from_detections(
            [{"start": 10.0, "end": 15.0}, {"start": 40.0, "end": 45.0}],
            100.0, pad_before=2.0, pad_after=2.5, merge_gap_seconds=1.5,
        )
        assert windows == [(8.0, 17.5), (38.0, 47.5)]
