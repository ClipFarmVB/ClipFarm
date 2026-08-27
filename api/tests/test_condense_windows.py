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
    _clear_previous_condensed,
    _condense_verdict_is_confident,
    _maybe_clear_previous_condensed,
    _worth_condensing,
)
from ml.pipeline.dead_time import Abstained                         # noqa: E402

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
        # Not just the shape: the api-side path must carry the sentinel through
        # too, because the stage reads it to decide both what to log and — since
        # the fifth review — whether it may delete a previous run's cut. List
        # equality alone would pass with the fact stripped somewhere in here.
        assert isinstance(windows, Abstained)


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


class TestClearingAPreviousCondensedCut:
    """A run that produces no cut must not leave the last one behind.

    Two halves, and the object half is the one with no other safety net:
    `delete_game` builds its delete list from `condensed_video_url`, so once
    that column is NULL nothing in the system can reach the old MP4 again. The
    row write is visible in the UI within a page load; an orphan is invisible
    and permanent.
    """

    def _spy(self, monkeypatch, *, previous="https://r2/old.mp4",
             row_raises=None, delete_raises=None):
        from app.services import storage as s3
        from app.workers import _sync_db

        calls = []

        def fake_clear(gid):
            calls.append(("row", gid))
            if row_raises:
                raise row_raises
            return previous

        def fake_delete(key):
            calls.append(("object", key))
            if delete_raises:
                raise delete_raises

        monkeypatch.setattr(_sync_db, "sync_clear_condensed_result", fake_clear)
        monkeypatch.setattr(s3, "delete_file", fake_delete)
        monkeypatch.setattr(s3, "condensed_key", lambda gid: f"condensed/{gid}.mp4")
        return calls

    def test_clears_the_row_and_deletes_the_object(self, monkeypatch):
        calls = self._spy(monkeypatch)

        _clear_previous_condensed("game-1")

        assert calls == [
            ("row", "game-1"),
            ("object", "condensed/game-1.mp4"),
        ], "both halves, row first"

    def test_no_previous_cut_means_no_delete(self, monkeypatch):
        """The row is the record of the object, so a NULL column means there is
        nothing out there to remove. Asking R2 about a key that never existed
        would make the cleanup warning meaningless noise on every no-cut run."""
        calls = self._spy(monkeypatch, previous=None)

        _clear_previous_condensed("game-4")

        assert calls == [("row", "game-4")]

    def test_a_failed_object_delete_does_not_break_the_stage(self, monkeypatch):
        """Reporting must never break processing: the game still ships. The
        orphan is logged rather than raised."""
        calls = self._spy(monkeypatch, delete_raises=RuntimeError("R2 down"))

        _clear_previous_condensed("game-2")

        assert [c[0] for c in calls] == ["row", "object"]

    def test_a_failed_row_write_leaves_the_object_alone(self, monkeypatch):
        """The reverse order would 404 the player: if the row still points at
        the object, the object has to still be there."""
        calls = self._spy(monkeypatch, row_raises=RuntimeError("db down"))

        with pytest.raises(RuntimeError):
            _clear_previous_condensed("game-3")

        assert [c[0] for c in calls] == ["row"], "no delete after a failed clear"


class TestOnlyAConfidentVerdictMayClear:
    """Clearing deletes the object as well as the row, so it is unrecoverable.

    A run that produced no cut has said one of two very different things:
    "I looked and there is nothing worth cutting", or "I could not look". Only
    the first earns the right to destroy the previous run's cut. The second
    happens for real — a tracking outage takes the pose-fallback branch, which
    can return no windows without raising anything.
    """

    def test_a_real_verdict_may_clear(self):
        assert _condense_verdict_is_confident([], ball_ok=True) is True
        assert _condense_verdict_is_confident(
            [(0.0, 10.0)], ball_ok=True,
        ) is True

    def test_a_run_without_ball_signal_may_not(self):
        """The pose-fallback branch: tracking failed, so 'no windows' is a
        statement about the tracker, not about the game."""
        assert _condense_verdict_is_confident([], ball_ok=False) is False

    def test_an_abstain_may_not(self):
        """The builder said outright that it could not judge the track. This is
        what Abstained is for — inferring it from the window shape would also
        match a genuine full-coverage condense."""
        abstained = Abstained([(0.0, 120.0)])
        assert _condense_verdict_is_confident(abstained, ball_ok=True) is False

    def test_the_shapes_alone_do_not_decide_it(self):
        """A plain whole-video window from a confident run is not an abstain."""
        assert _condense_verdict_is_confident([(0.0, 120.0)], ball_ok=True) is True


class TestTheClearIsActuallyGatedOnTheVerdict:
    """The wiring, not the predicate.

    The defect this guards is the one that actually shipped: the clear was
    unconditional. The predicate was never wrong — it did not exist. So testing
    `_condense_verdict_is_confident` four ways proves nothing about whether
    anything consults it, and the suite would stay green with the branch
    replaced by `if True:`. These tests fail in that case.
    """

    def _spy_clear(self, monkeypatch):
        from app.workers import tasks

        cleared = []
        monkeypatch.setattr(
            tasks, "_clear_previous_condensed", lambda gid: cleared.append(gid),
        )
        return cleared

    def test_a_confident_verdict_clears(self, monkeypatch):
        cleared = self._spy_clear(monkeypatch)

        attempted = _maybe_clear_previous_condensed(
            "game-1", [], ball_ok=True, built_by="guarded", _require_lock=lambda: None,
        )

        assert attempted is True
        assert cleared == ["game-1"]

    def test_a_run_without_ball_signal_deletes_nothing(self, monkeypatch):
        cleared = self._spy_clear(monkeypatch)

        attempted = _maybe_clear_previous_condensed(
            "game-2", [], ball_ok=False, built_by="pose-fallback",
            _require_lock=lambda: None,
        )

        assert attempted is False
        assert cleared == [], "a degraded run must not touch the previous cut"

    def test_an_abstain_deletes_nothing(self, monkeypatch):
        cleared = self._spy_clear(monkeypatch)

        attempted = _maybe_clear_previous_condensed(
            "game-3", Abstained([(0.0, DURATION)]),
            ball_ok=True, built_by="guarded", _require_lock=lambda: None,
        )

        assert attempted is False
        assert cleared == []

    def test_the_lock_is_checked_before_deleting(self, monkeypatch):
        """`_require_lock` guards every write in this stage; losing the lock
        mid-run means another worker owns the game and this one must not write.
        LockLost has to pass through, not be swallowed as a reporting failure."""
        from app.workers.locks import LockLost

        cleared = self._spy_clear(monkeypatch)

        def lost():
            raise LockLost("another worker owns this game")

        with pytest.raises(LockLost):
            _maybe_clear_previous_condensed(
                "game-4", [], ball_ok=True, built_by="guarded", _require_lock=lost,
            )

        assert cleared == []

    def test_a_failed_clear_does_not_break_the_stage(self, monkeypatch):
        """Reporting must never break processing — the game still ships."""
        from app.workers import tasks

        def boom(gid):
            raise RuntimeError("db down")

        monkeypatch.setattr(tasks, "_clear_previous_condensed", boom)

        assert _maybe_clear_previous_condensed(
            "game-5", [], ball_ok=True, built_by="guarded", _require_lock=lambda: None,
        ) is True
