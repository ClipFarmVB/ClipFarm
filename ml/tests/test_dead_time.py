"""Unit tests for keep-window derivation (ml/pipeline/dead_time.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.dead_time import (
    REFERENCE_FRAME_HEIGHT,
    active_windows_from_contacts,
    active_windows_from_detections,
    bridge_windows_by_motion,
    merge_intervals,
)


def test_reference_frame_height_matches_ball():
    """
    dead_time duplicates ball.REFERENCE_FRAME_HEIGHT rather than importing it,
    to stay dependency-light. The comment at that constant has always claimed
    this assertion existed; the CF-174 review found it never had been written,
    so the two could drift silently — the same class of unit mismatch CF-174 fixes.
    """
    from ml.pipeline.ball import REFERENCE_FRAME_HEIGHT as BALL_REFERENCE_FRAME_HEIGHT

    assert REFERENCE_FRAME_HEIGHT == BALL_REFERENCE_FRAME_HEIGHT


def contacts_at(*times: float) -> list[dict]:
    return [{"time": t} for t in times]


def ball_path(t_start: float, t_end: float, speed_pxps: float, step: float = 0.33) -> list[dict]:
    """Tracked positions moving in a straight line at speed_pxps."""
    out, t = [], t_start
    while t <= t_end:
        out.append({"time": t, "x": (t - t_start) * speed_pxps, "y": 0.0})
        t += step
    return out


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
            contacts_at(10, 12, 50), 100.0,
            min_contacts=2, pad_before=2.0, pad_after=2.5,
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

    # CF-46 default behavior: the loosened thresholds must not drop play.

    def test_default_keeps_single_contact_group(self):
        # An ace / tracking-starved rally surfaces as one contact — kept.
        windows = active_windows_from_contacts(contacts_at(10, 12, 50), 100.0)
        assert len(windows) == 2
        assert windows[1] == (45.0, 54.0)

    def test_default_survives_mid_rally_tracking_dropout(self):
        # A 9s detection gap inside one rally stays a single window
        # (gap_seconds=10.0; the CF-24 5.0 split it into fragments).
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 21, 23), 100.0,
        )
        assert windows == [(5.0, 27.0)]

    def test_default_merges_near_adjacent_windows(self):
        # Contacts 14s apart split into two groups, but the padded windows
        # (5, 16) and (21, 32) sit 5.0s apart — merge_gap_seconds=5.0 joins
        # them, keeping the short dead gap instead of jump-cutting.
        windows = active_windows_from_contacts(
            contacts_at(10, 12, 26, 28), 100.0,
        )
        assert windows == [(5.0, 32.0)]

    def test_default_lead_in_covers_serve(self):
        # Tracking often misses the serve contact; the first detection is the
        # receive. The default lead-in must reach back to the serve ritual.
        windows = active_windows_from_contacts(contacts_at(30, 32), 100.0)
        assert windows[0][0] <= 25.0


class TestBridgeWindowsByMotion:
    def test_bridges_fast_gap(self):
        # Ball flying fast through the gap = contact-silent play — one rally.
        positions = ball_path(10.0, 20.0, speed_pxps=300.0)
        windows = bridge_windows_by_motion([(0.0, 10.0), (20.0, 30.0)], positions)
        assert windows == [(0.0, 30.0)]

    def test_slow_gap_stays_cut(self):
        # Ball carried/tossed slowly between rallies — still dead time.
        positions = ball_path(10.0, 20.0, speed_pxps=50.0)
        windows = bridge_windows_by_motion([(0.0, 10.0), (20.0, 30.0)], positions)
        assert windows == [(0.0, 10.0), (20.0, 30.0)]

    def test_long_gap_never_bridges(self):
        # Fast shag throws inside a between-games break must not join it.
        positions = ball_path(10.0, 40.0, speed_pxps=300.0)
        windows = bridge_windows_by_motion(
            [(0.0, 10.0), (40.0, 50.0)], positions, max_bridge_seconds=20.0,
        )
        assert windows == [(0.0, 10.0), (40.0, 50.0)]

    def test_untracked_gap_is_no_evidence(self):
        # No positions inside the gap — nothing to justify bridging.
        positions = ball_path(0.0, 10.0, speed_pxps=300.0)
        windows = bridge_windows_by_motion([(0.0, 10.0), (20.0, 30.0)], positions)
        assert windows == [(0.0, 10.0), (20.0, 30.0)]

    def test_tracking_dropout_inside_gap_adds_no_fake_speed(self):
        # Two distant samples across a dropout must not register as one
        # huge-speed sample (max_sample_spacing guards this).
        positions = [
            {"time": 10.0, "x": 0.0, "y": 0.0},
            {"time": 19.0, "x": 5000.0, "y": 0.0},
        ]
        windows = bridge_windows_by_motion([(0.0, 10.0), (20.0, 30.0)], positions)
        assert windows == [(0.0, 10.0), (20.0, 30.0)]

    def test_chains_across_multiple_gaps(self):
        positions = ball_path(8.0, 42.0, speed_pxps=300.0)
        windows = bridge_windows_by_motion(
            [(0.0, 10.0), (15.0, 25.0), (30.0, 40.0)], positions,
        )
        assert windows == [(0.0, 40.0)]

    def test_single_window_unchanged(self):
        assert bridge_windows_by_motion([(0.0, 10.0)], ball_path(0, 10, 300)) == [(0.0, 10.0)]

    def test_empty_windows(self):
        assert bridge_windows_by_motion([], ball_path(0, 10, 300)) == []


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
