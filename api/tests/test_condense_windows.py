"""
CF-187: builder selection for the condense stage.

These cover the branch that decides *which* keep-windows ship, which is the part
with no other safety net: the guarded builder itself is unit-tested in
ml/tests/test_dead_time.py, and everything downstream is ffmpeg.

The distinction under test is that an empty result and a raised exception are
different answers. `[]` from the guarded builder is a verdict ("nothing here is
play"); only an exception means "this builder is broken, use the rules". A
future simplification to `if not windows:` passes every other test in the repo
and silently re-admits the contacts the speed gate just rejected.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `ml`

pytest.importorskip("celery")
pytest.importorskip("numpy")

from app.config import settings                                    # noqa: E402
from app.workers.tasks import (                                    # noqa: E402
    CONDENSE_MIN_TRIM_FRACTION,
    _build_condense_windows,
    _worth_condensing,
)

FRAME_H = 360
DURATION = 120.0


def track(rally=(10.0, 30.0), rally_speed=300.0, idle_speed=20.0, step=0.33,
          duration=DURATION):
    """Ball track spanning the whole video: fast during `rally`, idle otherwise."""
    out, t, x = [], 0.0, 0.0
    while t <= duration:
        out.append({"time": t, "x": x, "y": 100.0})
        x += (rally_speed if rally[0] <= t <= rally[1] else idle_speed) * step
        t += step
    return out


def contacts_at(*times):
    return [{"time": t} for t in times]


def covers(windows, t):
    return any(s <= t <= e for s, e in windows)


class TestBuilderSelection:
    def test_guarded_is_used_when_requested(self):
        windows, built_by = _build_condense_windows(
            "guarded", contacts_at(12.0, 15.0, 18.0), track(),
            DURATION, FRAME_H, settings,
        )
        assert built_by == "guarded"
        assert covers(windows, 15.0) and not covers(windows, 90.0)

    def test_rules_mode_skips_the_guarded_builder(self):
        contacts = contacts_at(12.0, 15.0, 18.0, 70.0)
        rules, built_by = _build_condense_windows(
            "rules", contacts, track(), DURATION, FRAME_H, settings,
        )
        assert built_by == "rules"
        # The idle-time contact survives without the speed gate — that is the
        # difference between the two modes, and the reason guarded is default.
        assert covers(rules, 70.0)

    def test_unknown_mode_falls_back_to_rules(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="app.workers.tasks"):
            windows, built_by = _build_condense_windows(
                "does-not-exist", contacts_at(12.0, 15.0), track(),
                DURATION, FRAME_H, settings,
            )
        assert built_by == "rules"
        assert windows, "the fallback must still produce windows"
        assert "does-not-exist" in caplog.text

    def test_guarded_failure_falls_back_to_rules(self, caplog):
        import logging
        # frame_height 0 makes active_windows_guarded raise: every speed
        # threshold is normalized by it, so it refuses rather than abstain.
        with caplog.at_level(logging.WARNING, logger="app.workers.tasks"):
            windows, built_by = _build_condense_windows(
                "guarded", contacts_at(12.0, 15.0), track(), DURATION, 0, settings,
            )
        assert built_by == "rules"
        assert windows
        assert "failed" in caplog.text


class TestEmptyIsAVerdictNotAFailure:
    """The regression this file exists for."""

    def test_guarded_empty_result_is_not_overridden_by_the_rules(self):
        # Dense track (3 samples/s, well over the abstain floor) that never moves
        # fast enough to gate a contact or anchor a window: warm-up footage.
        positions = track(rally=(0.0, 0.0), idle_speed=30.0)
        contacts = contacts_at(20.0, 25.0, 60.0, 90.0)

        windows, built_by = _build_condense_windows(
            "guarded", contacts, positions, DURATION, FRAME_H, settings,
        )
        assert windows == [], "the speed gate rejected every contact — that is the answer"
        assert built_by == "guarded"

        # And the rules path would have said something quite different, which is
        # exactly what must not leak through.
        rules, _ = _build_condense_windows(
            "rules", contacts, positions, DURATION, FRAME_H, settings,
        )
        assert rules, "sanity: the rules path does build windows from these contacts"

    def test_abstain_is_passed_through_intact(self):
        # ~0.83 samples/s: under condense_guard_min_track_rate, so the builder
        # declines and keeps the whole video.
        sparse = track(step=1.2)
        windows, built_by = _build_condense_windows(
            "guarded", contacts_at(20.0, 25.0), sparse, DURATION, FRAME_H, settings,
        )
        assert windows == [(0.0, DURATION)]
        assert built_by == "guarded"


class TestWorthCondensing:
    def test_abstain_is_not_worth_encoding(self):
        assert _worth_condensing(DURATION, DURATION) is False

    def test_a_real_trim_is(self):
        assert _worth_condensing(DURATION * 0.5, DURATION) is True

    def test_threshold_is_the_documented_fraction(self):
        just_under = DURATION * (1.0 - CONDENSE_MIN_TRIM_FRACTION) - 0.1
        just_over = DURATION * (1.0 - CONDENSE_MIN_TRIM_FRACTION) + 0.1
        assert _worth_condensing(just_under, DURATION) is True
        assert _worth_condensing(just_over, DURATION) is False

    def test_zero_duration_never_encodes(self):
        assert _worth_condensing(0.0, 0.0) is False


class TestPoseIsOptIn:
    """
    CF-198: the pose signal reaches the builder only when it is supplied, and
    the thresholds only when there is a signal to apply them to.
    """

    def pose_activity(self, active=(10.0, 30.0), duration=DURATION, step=0.33):
        """Activity samples: busy during `active`, still otherwise."""
        import numpy as np

        times, vals, t = [], [], 0.0
        while t <= duration:
            times.append(t)
            vals.append(2.0 if active[0] <= t <= active[1] else 0.05)
            t += step
        return np.array(times), np.array(vals)

    def test_no_pose_activity_is_the_cf187_behaviour(self):
        contacts, positions = contacts_at(12.0, 15.0, 18.0), track()
        with_none, built_by = _build_condense_windows(
            "guarded", contacts, positions, DURATION, FRAME_H, settings,
            pose_activity=None,
        )
        baseline, _ = _build_condense_windows(
            "guarded", contacts, positions, DURATION, FRAME_H, settings,
        )
        assert with_none == baseline
        assert built_by == "guarded"

    def test_supplying_pose_is_named_in_the_builder_label(self):
        """The log line has to distinguish a pose-assisted run from a plain one."""
        _, built_by = _build_condense_windows(
            "guarded", contacts_at(12.0, 15.0, 18.0), track(),
            DURATION, FRAME_H, settings, pose_activity=self.pose_activity(),
        )
        assert built_by == "guarded+pose"

    def test_pose_anchor_opens_a_window_with_no_contacts(self, monkeypatch):
        """
        The rally the ball detector never saw — CF-187's largest single source
        of removed live play.
        """
        monkeypatch.setattr(settings, "condense_pose_anchor_activity", 0.80, raising=False)
        windows, _ = _build_condense_windows(
            "guarded", [], track(rally_speed=20.0), DURATION, FRAME_H, settings,
            pose_activity=self.pose_activity(active=(40.0, 60.0)),
        )
        assert covers(windows, 50.0), "sustained player activity should open a window"

    def test_a_disabled_threshold_leaves_the_signal_inert(self, monkeypatch):
        """Both thresholds None is 'pose measured but not acted on'."""
        monkeypatch.setattr(settings, "condense_pose_anchor_activity", None, raising=False)
        monkeypatch.setattr(settings, "condense_pose_gate_activity", None, raising=False)
        contacts, positions = contacts_at(12.0, 15.0, 18.0), track()
        windows, _ = _build_condense_windows(
            "guarded", contacts, positions, DURATION, FRAME_H, settings,
            pose_activity=self.pose_activity(),
        )
        baseline, _ = _build_condense_windows(
            "guarded", contacts, positions, DURATION, FRAME_H, settings,
        )
        assert windows == baseline

    def test_pose_defaults_are_off_and_conservative(self):
        """
        The gate scored positive on the fixtures it was tuned on and negative on
        the held-out ones, so it ships disabled while the anchor ships armed —
        and the stage itself stays off until condense_use_pose is set.
        """
        assert settings.condense_use_pose is False
        assert settings.condense_pose_gate_activity is None
        assert settings.condense_pose_anchor_activity == 0.80
