"""Unit tests for keep-window derivation (ml/pipeline/dead_time.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.dead_time import (
    active_windows_from_contacts,
    active_windows_from_detections,
    active_windows_guarded,
    bridge_windows_by_motion,
    merge_intervals,
    motion_anchor_windows,
    speed_gate_contacts,
    speed_samples,
    track_is_usable,
)


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


# ── guarded path (CF-187) ──────────────────────────────────────────────────
# Speeds below are px/s at FRAME_H=360, so the frame-heights/s thresholds land
# at: gate 0.25 -> 90 px/s, anchor 0.30 -> 108 px/s, track-hop 1.11 -> 400 px/s.

FRAME_H = 360


def mixed_path(
    duration: float,
    rally: tuple[float, float] = (10.0, 30.0),
    rally_speed: float = 300.0,
    idle_speed: float = 20.0,
    step: float = 0.33,
) -> list[dict]:
    """
    A track that survives the whole video — fast during `rally`, barely moving
    otherwise. This is the realistic shape: the tracker keeps finding *a* ball
    (often a spare on the sideline) long after play stops.
    """
    out, t, x = [], 0.0, 0.0
    while t <= duration:
        out.append({"time": t, "x": x, "y": 100.0})
        x += (rally_speed if rally[0] <= t <= rally[1] else idle_speed) * step
        t += step
    return out


def covers(windows, t: float) -> bool:
    return any(start <= t <= end for start, end in windows)


class TestSpeedSamples:
    def test_normalizes_by_frame_height(self):
        _, speeds = speed_samples(ball_path(0, 5, 180.0), FRAME_H)
        assert speeds.size
        assert abs(float(speeds.mean()) - 0.5) < 1e-6   # 180 px/s / 360 px

    def test_dropout_contributes_no_sample(self):
        positions = [{"time": 0.0, "x": 0.0, "y": 0.0}, {"time": 10.0, "x": 100.0, "y": 0.0}]
        times, speeds = speed_samples(positions, FRAME_H)
        assert times.size == 0 and speeds.size == 0

    def test_track_hop_is_discarded_not_measured(self):
        # 900 px/s = 2.5 frame-heights/s, above MAX_PLAUSIBLE_SPEED_FH: the
        # tracker jumped to another object, so this is not the ball's speed.
        _, speeds = speed_samples(ball_path(0, 5, 900.0), FRAME_H)
        assert speeds.size == 0

    def test_missing_frame_height_yields_nothing(self):
        times, speeds = speed_samples(ball_path(0, 5, 180.0), 0)
        assert times.size == 0 and speeds.size == 0


class TestTrackIsUsable:
    def test_dense_track_is_usable(self):
        assert track_is_usable(speed_samples(ball_path(0, 60, 200.0), FRAME_H), 60.0)

    def test_sparse_track_is_not(self):
        # ~0.83 samples/s: under the 1.0/s floor, and under what the anchor needs.
        sparse = ball_path(0, 60, 200.0, step=1.2)
        assert not track_is_usable(speed_samples(sparse, FRAME_H), 60.0)

    def test_zero_duration_is_not_usable(self):
        assert not track_is_usable(speed_samples(ball_path(0, 5, 200.0), FRAME_H), 0.0)


class TestSpeedGateContacts:
    def test_keeps_contacts_on_a_moving_ball(self):
        samples = speed_samples(ball_path(0, 20, 300.0), FRAME_H)
        assert speed_gate_contacts(contacts_at(5.0, 10.0), samples) == contacts_at(5.0, 10.0)

    def test_drops_contacts_on_a_near_stationary_ball(self):
        # 20 px/s = 0.055 fh/s: a spare ball rolling in a cart, not play.
        samples = speed_samples(ball_path(0, 20, 20.0), FRAME_H)
        assert speed_gate_contacts(contacts_at(5.0, 10.0), samples) == []

    def test_no_speed_samples_gates_nothing(self):
        empty = speed_samples([], FRAME_H)
        assert speed_gate_contacts(contacts_at(5.0), empty) == contacts_at(5.0)

    def test_contact_outside_the_tracked_stretch_is_dropped(self):
        samples = speed_samples(ball_path(0, 20, 300.0), FRAME_H)
        assert speed_gate_contacts(contacts_at(200.0), samples) == []


class TestMotionAnchorWindows:
    def test_sustained_fast_motion_opens_a_window(self):
        samples = speed_samples(mixed_path(120.0), FRAME_H)
        windows = motion_anchor_windows(samples, 120.0)
        assert covers(windows, 20.0), "the 10-30s rally should anchor a window"
        assert not covers(windows, 90.0), "idle stretches should not"

    def test_slow_motion_opens_nothing(self):
        samples = speed_samples(ball_path(0, 120, 20.0), FRAME_H)
        assert motion_anchor_windows(samples, 120.0) == []

    def test_brief_burst_is_below_min_seconds(self):
        samples = speed_samples(mixed_path(60.0, rally=(10.0, 10.6)), FRAME_H)
        assert motion_anchor_windows(samples, 60.0) == []

    def test_no_samples_no_windows(self):
        assert motion_anchor_windows(speed_samples([], FRAME_H), 60.0) == []


class TestActiveWindowsGuarded:
    def test_abstains_on_a_sparse_track(self):
        sparse = ball_path(0, 120, 300.0, step=1.2)
        windows = active_windows_guarded(contacts_at(20.0, 25.0), sparse, 120.0, FRAME_H)
        assert windows == [(0.0, 120.0)], "too little signal to condense on — keep everything"

    def test_keeps_the_rally_and_cuts_the_dead_time(self):
        positions = mixed_path(120.0)
        windows = active_windows_guarded(
            contacts_at(12.0, 15.0, 18.0), positions, 120.0, FRAME_H,
        )
        assert covers(windows, 15.0)
        assert not covers(windows, 90.0)
        assert sum(e - s for s, e in windows) < 120.0, "an abstain would keep everything"

    def test_contact_over_a_stationary_ball_opens_no_window(self):
        positions = mixed_path(120.0)
        # t=70 sits in the idle stretch: the contact is tracker jitter, and
        # nothing else vouches for play there.
        windows = active_windows_guarded(contacts_at(70.0), positions, 120.0, FRAME_H)
        assert not covers(windows, 70.0)

    def test_rally_with_no_contacts_is_still_kept(self):
        # The failure the anchor exists for: 19 of test4's 46 rallies produce no
        # contact at all, and in the rule-based path that play is cut outright.
        positions = mixed_path(120.0)
        assert covers(active_windows_guarded([], positions, 120.0, FRAME_H), 20.0)

    def test_zero_duration(self):
        assert active_windows_guarded(contacts_at(1.0), mixed_path(10.0), 0.0, FRAME_H) == []

    def test_dense_but_lifeless_track_returns_no_windows(self):
        # Warm-up footage: the ball is tracked densely (3 samples/s, well over
        # the abstain floor) but never moves fast enough to gate a contact or
        # anchor a window. [] here is a verdict — "no play" — not a failure, and
        # the condense stage must not read it as one and rebuild windows from
        # the contacts the gate just rejected.
        positions = mixed_path(120.0, rally=(0.0, 0.0), idle_speed=30.0)
        assert track_is_usable(speed_samples(positions, FRAME_H), 120.0)
        assert active_windows_guarded(
            contacts_at(20.0, 25.0, 60.0, 90.0), positions, 120.0, FRAME_H,
        ) == []

    def test_anchor_pad_is_independent_of_the_contact_pads(self):
        # Raising pad_before must not widen an anchor-recovered rally: the two
        # pads cover different things, and the anchor already spans its motion.
        positions = mixed_path(120.0)
        base = active_windows_guarded([], positions, 120.0, FRAME_H)
        wide = active_windows_guarded([], positions, 120.0, FRAME_H, pad_before=30.0)
        assert base == wide
        padded = active_windows_guarded([], positions, 120.0, FRAME_H, anchor_pad=6.0)
        assert padded != base, "anchor_pad is the knob that does move it"

    def test_missing_frame_height_raises_rather_than_abstaining(self):
        # Speeds are normalized by it, so 0 would turn every game into an
        # abstain; the condense stage catches this and uses the rules instead.
        with pytest.raises(ValueError, match="frame height"):
            active_windows_guarded(contacts_at(5.0), mixed_path(60.0), 60.0, 0)

    def test_no_positions_abstains(self):
        assert active_windows_guarded(contacts_at(5.0), [], 60.0, FRAME_H) == [(0.0, 60.0)]
