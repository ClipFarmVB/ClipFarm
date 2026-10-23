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


def learned_weights(**over) -> dict:
    """Synthetic weights that call fast seconds in play (mean_speed_3s only),
    so their windows follow `track()`'s rally and differ from guarded's."""
    from ml.pipeline.dead_time_ml import FEATURE_NAMES, FEATURE_VERSION, SCALING, context_rule

    coef = [0.0] * len(FEATURE_NAMES)
    coef[FEATURE_NAMES.index("mean_speed_3s")] = 4.0
    data = {
        "features": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION,
        "scaling": dict(SCALING), "context": context_rule([]),
        "coef": coef, "intercept": -2.0, "threshold": 0.5,
        "validated": True, "trained_on": "synthetic",
    }
    data.update(over)
    return data


class TestLearnedMode:
    """CF-394: "learned" chains to "guarded", which chains to "rules".

    The acceptance criteria, one test each: committed (unvalidated) weights give
    guarded's windows and one log line naming why; validated weights give the
    learned builder's; a corrupted file gives guarded's.
    """

    CONTACTS = contacts_at(12.0, 15.0, 18.0, 70.0)

    @staticmethod
    def _weights_file(monkeypatch, tmp_path, text):
        import ml.pipeline.dead_time_ml as dml

        p = tmp_path / "weights.json"
        p.write_text(text)
        monkeypatch.setattr(dml, "WEIGHTS_PATH", p)
        return p

    def _build(self, mode, positions=None, frame_height=FRAME_H):
        return _build_condense_windows(
            mode, self.CONTACTS, track() if positions is None else positions,
            DURATION, frame_height, settings,
        )

    def test_the_committed_unvalidated_weights_give_guarded_windows(self, caplog):
        import logging

        import ml.pipeline.dead_time_ml as dml

        assert dml.load_weights(dml.WEIGHTS_PATH, warn_unvalidated=False)["validated"] is False, (
            "premise: the committed weights are unvalidated until CF-395"
        )
        guarded, _ = self._build("guarded")
        with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
            windows, built_by = self._build("learned")

        assert built_by == "guarded"
        assert windows == guarded
        lines = [r for r in caplog.records if r.name == "app.workers.tasks"]
        assert len(lines) == 1, [r.getMessage() for r in lines]
        assert lines[0].levelno == logging.INFO, "an expected fallback, not a failure"
        assert "not validated" in lines[0].getMessage()

    def test_validated_weights_give_the_learned_windows(self, monkeypatch, tmp_path):
        import json

        from ml.pipeline.dead_time_ml import active_windows_learned

        self._weights_file(monkeypatch, tmp_path, json.dumps(learned_weights()))
        windows, built_by = self._build("learned")

        assert built_by == "learned"
        assert windows == active_windows_learned(
            self.CONTACTS, track(), DURATION, FRAME_H, weights=learned_weights())
        guarded, _ = self._build("guarded")
        assert windows != guarded, "sanity: the two builders disagree on this track"

    @pytest.mark.parametrize("text", [
        "{not json",
        "",
        '{"features": ["only_one"], "validated": true}',
    ], ids=["truncated", "empty", "wrong-contract"])
    def test_a_corrupted_file_gives_guarded_windows(self, monkeypatch, tmp_path, caplog, text):
        import logging

        self._weights_file(monkeypatch, tmp_path, text)
        guarded, _ = self._build("guarded")
        with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
            windows, built_by = self._build("learned")

        assert (windows, built_by) == (guarded, "guarded")
        lines = [r for r in caplog.records if r.name == "app.workers.tasks"]
        assert len(lines) == 1 and lines[0].levelno == logging.WARNING

    def test_a_missing_file_gives_guarded_windows(self, monkeypatch, tmp_path):
        import ml.pipeline.dead_time_ml as dml

        monkeypatch.setattr(dml, "WEIGHTS_PATH", tmp_path / "absent.json")
        guarded, _ = self._build("guarded")
        assert self._build("learned") == (guarded, "guarded")

    def test_an_import_failure_gives_guarded_windows(self, monkeypatch):
        """The learned builder is its own module: unlike guarded → rules, its
        import failing still leaves the next link standing."""
        import ml.pipeline

        monkeypatch.delattr(ml.pipeline, "dead_time_ml", raising=False)
        monkeypatch.setitem(sys.modules, "ml.pipeline.dead_time_ml", None)
        guarded, _ = self._build("guarded")
        assert self._build("learned") == (guarded, "guarded")

    def test_learned_empty_is_a_verdict_not_a_fallback(self, monkeypatch, tmp_path):
        """One link up from TestEmptyIsAVerdictNotAFailure: `[]` from the learned
        builder ships as `[]`, not as guarded's windows."""
        import json

        self._weights_file(monkeypatch, tmp_path, json.dumps(learned_weights(threshold=2.0)))
        assert self._build("learned") == ([], "learned")

    def test_a_sparse_track_falls_back_to_guardeds_abstain(self, monkeypatch, tmp_path, caplog):
        """Declined, not scored — so the previous condensed cut survives, which
        it would not if the learned builder returned `[]` here."""
        import json
        import logging

        self._weights_file(monkeypatch, tmp_path, json.dumps(learned_weights()))
        with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
            windows, built_by = self._build("learned", positions=track(step=1.2))

        assert built_by == "guarded"
        assert isinstance(windows, Abstained)
        lines = [r for r in caplog.records if r.name == "app.workers.tasks"]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO

    def test_both_failing_lands_on_rules(self, monkeypatch, tmp_path):
        """frame_height 0 makes both track builders raise; the chain ends at
        rules, which needs no frame height."""
        import json

        self._weights_file(monkeypatch, tmp_path, json.dumps(learned_weights()))
        windows, built_by = self._build("learned", frame_height=0)
        assert built_by == "rules"
        assert windows

    def test_a_logging_failure_cannot_break_condense(self, monkeypatch):
        """Reporting must never break processing (CLAUDE.md → Code posture)."""
        from app.workers import tasks

        def broken(*a, **k):
            raise RuntimeError("log handler down")

        guarded, _ = self._build("guarded")
        monkeypatch.setattr(tasks.logger, "info", broken)
        monkeypatch.setattr(tasks.logger, "warning", broken)
        assert self._build("learned") == (guarded, "guarded")

    def test_unknown_mode_still_lands_on_rules(self):
        """The new link must not turn an unknown name into a guarded run."""
        assert self._build("Learned")[1] == "rules"


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


class TestTheCF174KillSwitchReachesTheBridge:
    """
    `ball_contact_scale_enabled` gates find_contacts *and* the condense motion
    bridge, and this is the wiring for the second half.

    It is one setting on purpose. The lever exists for incident response, so off
    has to mean `main`'s condense behaviour; gating only the contacts would leave
    the bridge scaled and hand an operator `main`'s contacts joined at 3x — a
    third combination nothing has ever scored, and the worst possible thing to
    discover mid-incident. Nothing else in this suite would notice the kwarg
    being dropped: the bridge is reached through two layers of builder.
    """

    def _bridge_threshold(self, monkeypatch, *, enabled, frame_height):
        """The speed_pxps bridge_windows_by_motion is actually reached with."""
        import ml.pipeline.dead_time as dt
        seen = {}
        real = dt.bridge_windows_by_motion

        def spy(windows, positions, **kw):
            seen.update(kw)
            return real(windows, positions, **kw)

        monkeypatch.setattr(dt, "bridge_windows_by_motion", spy)
        monkeypatch.setattr(settings, "ball_contact_scale_enabled", enabled)
        _build_condense_windows(
            "rules", contacts_at(12.0, 15.0, 18.0, 70.0), track(),
            DURATION, frame_height, settings,
        )
        assert "normalize" in seen, (
            "_build_condense_windows stopped passing normalize= to the bridge, "
            "so the kill switch no longer reaches the condense half"
        )
        # A copy, not `seen` itself: a second call in the same test wraps this
        # spy, so the live dict would keep updating and compare equal to any
        # later capture (R9-M1).
        return dict(seen)

    def test_on_scales_the_bridge_with_frame_height(self, monkeypatch):
        seen = self._bridge_threshold(monkeypatch, enabled=True, frame_height=1080)
        assert seen["normalize"] is True
        assert seen["frame_height"] == 1080

    def test_off_hands_the_bridge_mains_unscaled_threshold(self, monkeypatch):
        seen = self._bridge_threshold(monkeypatch, enabled=False, frame_height=1080)
        assert seen["normalize"] is False
        # The height is still passed; `normalize=False` is what stops it being
        # used, so the two halves cannot disagree about which video this is.
        assert seen["frame_height"] == 1080

    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_soft_flag_does_not_reach_the_bridge(self, monkeypatch, enabled):
        """
        CF-376's `ball_contact_soft_scale_enabled` softens the contact gates
        and nothing else: the bridge keeps the full scale, so it is handed the
        same arguments whichever way the soft flag is set. Soft contacts joined
        by a full-scale bridge is the combination that flag ships, measured on
        unlabelled fixtures in its PR.
        """
        monkeypatch.setattr(settings, "ball_contact_soft_scale_enabled", False)
        baseline = self._bridge_threshold(monkeypatch, enabled=enabled, frame_height=1080)
        monkeypatch.setattr(settings, "ball_contact_soft_scale_enabled", True)
        soft = self._bridge_threshold(monkeypatch, enabled=enabled, frame_height=1080)
        assert "soft_scale" not in soft
        assert soft == baseline


def test_the_worker_hands_find_contacts_both_switches():
    """
    process_game_task is the one production caller of find_contacts, and the
    soft flag is a keyword with a False default, so dropping it there is silent
    — the flag would read as set and change nothing. Asserted on the parsed
    call, as test_eval_condense_settings does for the eval mirrors.
    """
    import ast

    tasks_py = Path(__file__).resolve().parents[1] / "app" / "workers" / "tasks.py"
    tree = ast.parse(tasks_py.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "find_contacts"
    ]
    assert calls, "tasks.py no longer calls find_contacts"
    for call in calls:
        kwargs = {k.arg: ast.unparse(k.value) for k in call.keywords if k.arg}
        assert kwargs.get("normalize") == "_cfg.ball_contact_scale_enabled"
        assert kwargs.get("soft_scale") == "_cfg.ball_contact_soft_scale_enabled"


def test_the_worker_filters_static_sites_before_anything_reads_the_track():
    """
    CF-229: process_game_task applies `ball_static_site_filter_enabled` to the
    cached track once, rebinding `tracker`, before the find_contacts call that
    reads it — so contacts and the condense stage's positions, which are both
    read off `tracker` afterwards, see the same track. Dropping `enabled=`
    would not even run (it is required), but reading the wrong setting, or
    filtering into a name nothing reads, would be silent.
    """
    import ast

    tasks_py = Path(__file__).resolve().parents[1] / "app" / "workers" / "tasks.py"
    tree = ast.parse(tasks_py.read_text(encoding="utf-8"))
    assigns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name) and n.value.func.id == "filter_static_sites"
    ]
    assert len(assigns) == 1, "tasks.py should filter the ball track exactly once"
    (assign,) = assigns
    assert [ast.unparse(t) for t in assign.targets] == ["tracker"]
    call = assign.value
    assert ast.unparse(call.args[0]) == "tracker"
    kwargs = {k.arg: ast.unparse(k.value) for k in call.keywords if k.arg}
    assert kwargs.get("enabled") == "_cfg.ball_static_site_filter_enabled"

    finds = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "find_contacts"
    ]
    assert finds and all(ast.unparse(f.args[0]) == "tracker" for f in finds)
    assert all(assign.lineno < f.lineno for f in finds), (
        "find_contacts runs before the static-site filter, so it reads the unfiltered track"
    )


def test_the_static_site_filter_ships_off():
    """CF-229: the flag is evidence-gathering only; flipping it is a decision."""
    from app.config import Settings

    assert Settings.model_fields["ball_static_site_filter_enabled"].default is False
