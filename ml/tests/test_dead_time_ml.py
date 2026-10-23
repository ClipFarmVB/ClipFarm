"""
Feature extraction for the learned dead-time builder (CF-392).

No conftest.py exists anywhere in this repo, so every file in ml/tests puts the
project root on sys.path itself. Matching test_dead_time.py.
"""
import ast
import inspect
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import deadtime_variants as dv  # noqa: E402
from ml.eval import tune_contacts as tc  # noqa: E402
from ml.pipeline import dead_time as dt  # noqa: E402
from ml.pipeline.dead_time_ml import (  # noqa: E402
    CONTACT_GAP_CAP,
    CONTEXT_LAGS,
    DECODERS,
    EMPTY_MEAN_Y,
    FAST_SPEED_FH,
    FEATURE_NAMES,
    FEATURE_VERSION,
    SCALING,
    WEIGHTS_PATH,
    TrackTooSparseError,
    UnvalidatedWeightsError,
    active_windows_learned,
    add_context,
    check_contract,
    compute_features,
    context_rule,
    decode_windows,
    fit_transitions,
    load_weights,
    predict_in_play,
    robust_scale,
    smooth_probability,
    viterbi_path,
    viterbi_windows,
    windows_from_probability,
)

H = 360  # frame height for every fixture below


def col(name: str) -> int:
    return FEATURE_NAMES.index(name)


def track(times, *, x=0.0, y=180.0, conf=0.9, step=0.0):
    """Samples at `times`, advancing x by `step` per sample."""
    return [
        {"time": t, "x": x + i * step, "y": y, "confidence": conf}
        for i, t in enumerate(times)
    ]


class TestContract:
    def test_thirteen_columns_at_version_four(self):
        assert len(FEATURE_NAMES) == 13
        assert FEATURE_VERSION == 4
        assert len(set(FEATURE_NAMES)) == 13, "a duplicated column name"

    def test_the_fast_bar_is_the_anchors_bar(self):
        """One of five copies of 0.30, pinned to the one that defines it.

        The five, so the two files carrying this count say the same list:
        `motion_anchor_windows(speed=...)`'s default, which defines it and is
        what this pins; `FAST_SPEED_FH` in ml/pipeline/dead_time_ml.py, the
        copy this test exists for; `active_windows_guarded(anchor_speed=...)`,
        which passes it down; `condense_guard_anchor_speed` in
        api/app/config.py, which is what production actually sends; and a
        hardcoded `speed=0.30` in ml/eval/deadtime_variants.py:169.

        This pin reaches exactly one of the five — `motion_anchor_windows`'
        default — by comparing this module's copy against it. The other three
        are unpinned.

        `motion_anchor_windows`' default is the same quantity — the per-sample
        "this is fast" bar — and nothing but this test stops the two drifting.
        Read from the live signature rather than restated, so a change there
        fails here instead of silently giving the two columns different ideas of
        fast.
        """
        anchor = inspect.signature(dt.motion_anchor_windows).parameters["speed"].default
        assert FAST_SPEED_FH == anchor

    def test_it_is_not_the_contact_gate_bar(self):
        """`gate_speed` (0.25) is a median over ±1.5s judging a contact, not a
        per-sample fast bar. dead_time.py's own comments warn against conflating
        the family; this keeps that mistake from being made quietly."""
        gate = inspect.signature(dt.active_windows_guarded).parameters["gate_speed"].default
        assert FAST_SPEED_FH != gate


class TestSeparation:
    """The acceptance criterion: an airborne rally and a carried-ball break
    separate on mean_y_5s and mean_speed_3s."""

    def _matrix(self):
        # 0-10s: rally — ball high in frame (y=60 of 360) and moving 300 px/s.
        # 20-30s: break — ball carried low (y=300) and barely moving.
        rally = track([t / 2 for t in range(0, 21)], x=0.0, y=60.0, step=150.0)
        carry = track([20 + t / 2 for t in range(0, 21)], x=0.0, y=300.0, step=10.0)
        return compute_features(rally + carry, [], 31.0, H)

    def test_the_rally_is_higher_in_frame_than_the_carry(self):
        f = self._matrix()
        assert f[5, col("mean_y_5s")] < f[25, col("mean_y_5s")]
        # And on the right side of centre, not merely ordered.
        assert f[5, col("mean_y_5s")] < EMPTY_MEAN_Y < f[25, col("mean_y_5s")]

    def test_the_rally_is_faster_than_the_carry(self):
        f = self._matrix()
        assert f[5, col("mean_speed_3s")] > f[25, col("mean_speed_3s")]
        assert f[5, col("fast_fraction_5s")] > f[25, col("fast_fraction_5s")]


class TestAlwaysFinite:
    """Every degenerate input still gives a finite matrix of the right shape."""

    @pytest.mark.parametrize(
        "positions,contacts,duration",
        [
            ([], [], 10.0),                              # empty track
            (track([5.0]), [], 10.0),                    # a single sample
            ([], [{"time": 3.0}], 10.0),                 # contacts but no track
            (track([0.1, 0.2]), [], 0.5),                # duration under a second
        ],
        ids=["empty", "single-sample", "contacts-only", "sub-second"],
    )
    def test_degenerate_inputs(self, positions, contacts, duration):
        f = compute_features(positions, contacts, duration, H)
        assert f.shape == (max(1, math.ceil(duration)), 13)
        assert np.isfinite(f).all(), "a NaN or inf reached the matrix"

    def test_an_all_over_ceiling_stretch_is_finite(self):
        """The NaN path, and the reason this module does not use a plain cumsum.

        `speed_samples` returns NaN for a displacement above
        MAX_PLAUSIBLE_SPEED_FH rather than dropping it. A cumulative sum carries
        that NaN to every window whose right edge reaches past it — including
        windows centred BEFORE it — so the naive helper would return a matrix
        that is NaN almost everywhere.
        """
        # Each hop is 2000px in 0.5s = 4000px/s = 11.1 fh/s, far over the 1.11
        # ceiling, so every speed sample is NaN.
        pos = track([t / 2 for t in range(0, 41)], x=0.0, y=180.0, step=2000.0)
        _, speeds = dt.speed_samples(pos, H)
        assert len(speeds) and np.isnan(speeds).all(), "fixture no longer all over-ceiling"

        f = compute_features(pos, [], 20.0, H)
        assert np.isfinite(f).all()
        # Unjudgeable magnitudes reach the empty-window fill...
        assert f[:, col("mean_speed_3s")].max() == 0.0
        assert f[:, col("max_speed_3s")].max() == 0.0
        # ...while the anchor's convention counts them as not-fast rather than
        # masking them out, so the fraction is 0 with a non-zero denominator.
        assert f[:, col("fast_fraction_5s")].max() == 0.0
        # The track itself is still dense — those samples exist.
        assert f[:, col("track_rate_2s")].max() > 0.0

    def test_one_bad_hop_does_not_poison_its_neighbours(self):
        """The specific failure a cumulative sum produces: a single NaN in the
        middle makes every LATER window NaN, and every window whose right edge
        passes it, which includes windows centred earlier."""
        pos = track([t / 2 for t in range(0, 41)], x=0.0, y=180.0, step=50.0)
        pos[20]["x"] = 99999.0  # one unbelievable hop, mid-track
        f = compute_features(pos, [], 20.0, H)
        assert np.isfinite(f).all()
        # Seconds well before and well after still measure real motion.
        assert f[2, col("mean_speed_3s")] > 0.0
        assert f[17, col("mean_speed_3s")] > 0.0


class TestFills:
    def test_an_unseen_second_sits_at_frame_centre_not_at_the_top(self):
        """mean_y_5s's fill is the one that inverts if it is left at zero.

        Image y grows downward, so 0.0 means "ball at the top of the frame" —
        airborne, in play. Filling an evidence-free second with it would make
        the emptiest possible data the strongest in-play vote on the column the
        epic measured at +0.17 AUC.
        """
        f = compute_features([], [], 5.0, H)
        assert (f[:, col("mean_y_5s")] == EMPTY_MEAN_Y).all()
        assert EMPTY_MEAN_Y == 0.5

        # A real ball high in frame must still read lower than the fill, or the
        # fill is not neutral with respect to the signal.
        high = compute_features(track([2.0, 2.5, 3.0], y=30.0), [], 5.0, H)
        assert high[2, col("mean_y_5s")] < EMPTY_MEAN_Y

    def test_contact_gaps_saturate_when_there_are_no_contacts(self):
        f = compute_features(track([1.0, 2.0]), [], 5.0, H)
        assert (f[:, col("since_contact")] == CONTACT_GAP_CAP).all()
        assert (f[:, col("until_contact")] == CONTACT_GAP_CAP).all()

    def test_max_conf_is_a_real_reduction_not_a_copy_of_the_mean(self):
        pos = track([0.5, 1.0, 1.5], conf=0.2)
        pos[1]["confidence"] = 0.95
        f = compute_features(pos, [], 3.0, H)
        assert f[1, col("max_conf_3s")] > f[1, col("mean_conf_3s")]
        assert f[1, col("max_conf_3s")] == pytest.approx(0.95)

    def test_max_speed_is_a_real_reduction_not_a_copy_of_the_mean(self):
        """The other half, which the plural-named version of this test never
        touched: `feats[:, 12] = mean_speed_3s` shipped green, while the commit
        message claimed the max-vs-mean revert was caught. It was caught for
        conf only.
        """
        # Alternating step lengths, so max and mean genuinely differ. Both must
        # stay BELIEVABLE: 200px in 0.5s at 360p is 400/360 = 1.1111 fh/s,
        # which is ABOVE MAX_PLAUSIBLE_SPEED_FH (1.11), so every fast sample
        # became NaN and was masked — mean and max then collapsed to the same
        # value and the test asserted nothing. 150px/0.5s = 0.83 fh/s is fast
        # and believable.
        #
        # This comment said "exactly MAX_PLAUSIBLE_SPEED_FH", and both halves of
        # that were wrong: 1.1111 is not 1.11, and the mask is `v <= max_speed`,
        # so a sample sitting EXACTLY on the ceiling is KEPT. Only strictly
        # above is unjudged. A near-miss described as an equality, in the
        # comment explaining why the fixture was degenerate.
        times = [t / 2 for t in range(0, 9)]
        pos = [{"time": t, "x": 0.0, "y": 180.0, "confidence": 0.9} for t in times]
        for i in range(1, len(pos)):
            pos[i]["x"] = pos[i - 1]["x"] + (150.0 if i % 2 else 20.0)
        _, speeds = dt.speed_samples(pos, H)
        assert np.isfinite(speeds).all(), "fixture went over the ceiling again"
        f = compute_features(pos, [], 5.0, H)
        assert f[2, col("max_speed_3s")] > f[2, col("mean_speed_3s")]

    def test_the_missing_confidence_field_is_reported(self, caplog):
        """Since CF-420 every producer forwards `confidence`, so this warning
        no longer describes the normal state — it means a producer regressed,
        or the track came from a dump written before CF-420. It still has to
        fire, because 0.0 is also these columns' empty-window fill: without it,
        "not supplied" and "no samples" are indistinguishable in the matrix and
        the trainer would silently learn a constant column."""
        with caplog.at_level("WARNING"):
            compute_features(
                [{"time": 1.0, "x": 0.0, "y": 180.0}], [], 3.0, H
            )
        assert "confidence" in caplog.text

    def test_no_warning_when_confidence_is_supplied(self, caplog):
        with caplog.at_level("WARNING"):
            compute_features(track([1.0]), [], 3.0, H)
        assert "confidence" not in caplog.text


class TestWeightsContract:
    """Rewritten rather than ported: there is no committed weights file on main
    (training is CF-393), so the branch's tests that call a bare `load_weights()`
    cannot run, and its own weights file is feature_version 2 with ten columns —
    this contract check would reject it."""

    @staticmethod
    def _write(tmp_path, **over):
        data = {
            "features": FEATURE_NAMES,
            "feature_version": FEATURE_VERSION,
            "scaling": dict(SCALING),
            "context": context_rule([]),
            "validated": True,
            "trained_on": "synthetic",
        }
        data.update(over)
        p = tmp_path / "w.json"
        p.write_text(json.dumps(data))
        return p

    def test_a_matching_file_loads(self, tmp_path):
        assert load_weights(self._write(tmp_path))["feature_version"] == FEATURE_VERSION

    def test_a_different_feature_list_is_rejected(self, tmp_path):
        p = self._write(tmp_path, features=FEATURE_NAMES[:-1])
        with pytest.raises(ValueError, match="trained for features"):
            load_weights(p)

    def test_a_different_feature_version_is_rejected(self, tmp_path):
        p = self._write(tmp_path, feature_version=FEATURE_VERSION - 1)
        with pytest.raises(ValueError, match="retrain"):
            load_weights(p)

    def test_the_branchs_v2_weights_would_be_rejected(self, tmp_path):
        """The concrete reason the old weights file was not copied over."""
        p = self._write(
            tmp_path,
            features=[n for n in FEATURE_NAMES if n not in
                      ("mean_y_5s", "max_conf_3s", "max_speed_3s")],
            feature_version=2,
        )
        with pytest.raises(ValueError):
            load_weights(p)

    @pytest.mark.parametrize("field,value", [
        ("zero_range", "divide_by_1"),
        ("range", "p75-p25"),
        ("rule", "global_standard"),
    ])
    def test_a_different_scaling_rule_is_refused(self, tmp_path, field, value):
        """R3-M1: the column count cannot see a scaling change; this record can."""
        p = self._write(tmp_path, scaling={**SCALING, field: value})
        with pytest.raises(ValueError, match="scaled by"):
            load_weights(p)

    def test_a_missing_scaling_record_is_refused(self, tmp_path):
        p = self._write(tmp_path)
        data = json.loads(p.read_text())
        del data["scaling"]
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="scaled by"):
            load_weights(p)

    def test_a_different_context_edge_is_refused(self, tmp_path):
        p = self._write(tmp_path, context={**context_rule([]), "edge": "zero"})
        with pytest.raises(ValueError, match="context"):
            load_weights(p)

    def test_the_record_describes_what_robust_scale_does(self):
        """
        The record is a literal, so it moves only when someone moves it. Change
        it with `robust_scale` (median, p10-p90, then max - min, then 1 — pinned
        in TestRobustScale), and the committed weights stop loading until they
        are retrained.
        """
        assert SCALING == {
            "rule": "per_video_robust",
            "center": "median",
            "range": "p90-p10",
            "zero_range": "max-min, then divide_by_1",
        }
        assert context_rule([-1, 1]) == {
            "lags": [-1, 1], "edge": "replicate",
            "layout": "base columns, then one block of all of them per lag, in lags order",
        }

    def test_unvalidated_weights_load_but_warn(self, tmp_path, caplog):
        p = self._write(tmp_path, validated=False)
        with caplog.at_level("WARNING"):
            load_weights(p)
        assert "NOT validated" in caplog.text

    def test_the_warning_can_be_turned_off(self, tmp_path, caplog):
        """The condense stage logs the refusal itself; a second line is noise."""
        p = self._write(tmp_path, validated=False)
        with caplog.at_level("WARNING"):
            assert load_weights(p, warn_unvalidated=False)["validated"] is False
        assert "NOT validated" not in caplog.text

    def test_check_contract_holds_a_dict_to_the_same_rules(self, tmp_path):
        """A dict that never went through `load_weights` — the builder's
        `weights=` — is refused for the same reasons, with the same words."""
        good = json.loads(self._write(tmp_path).read_text())
        check_contract(good)
        for over, match in [
            ({"features": FEATURE_NAMES[:-1]}, "trained for features"),
            ({"feature_version": FEATURE_VERSION - 1}, "retrain"),
            ({"scaling": {**SCALING, "range": "p75-p25"}}, "scaled by"),
            ({"context": {"lags": []}}, "context"),
        ]:
            with pytest.raises(ValueError, match=match):
                check_contract({**good, **over})


def _rally_track(rally=(10.0, 30.0), duration=120.0, step=0.33,
                 rally_speed=300.0, idle_speed=20.0):
    """Dense track (3 samples/s) over the whole video, fast inside `rally`."""
    out, t, x = [], 0.0, 0.0
    while t <= duration:
        out.append({"time": t, "x": x, "y": 180.0, "confidence": 0.9})
        x += (rally_speed if rally[0] <= t <= rally[1] else idle_speed) * step
        t += step
    return out


def learned_weights(**over) -> dict:
    """Synthetic weights that call fast seconds in play: everything rides on
    mean_speed_3s, so the expected windows follow the rally in `_rally_track`."""
    coef = [0.0] * len(FEATURE_NAMES)
    coef[col("mean_speed_3s")] = 4.0
    data = {
        "features": list(FEATURE_NAMES),
        "feature_version": FEATURE_VERSION,
        "scaling": dict(SCALING),
        "context": context_rule([]),
        "coef": coef,
        "intercept": -2.0,
        "threshold": 0.5,
        "validated": True,
        "trained_on": "synthetic",
    }
    data.update(over)
    return data


class TestActiveWindowsLearned:
    DURATION = 120.0

    def _run(self, *, positions=None, contacts=(), duration=None, frame_height=H, **kw):
        kw.setdefault("weights", learned_weights())
        return active_windows_learned(
            list(contacts), _rally_track() if positions is None else positions,
            self.DURATION if duration is None else duration, frame_height, **kw,
        )

    def test_windows_follow_the_probability_trace(self):
        windows = self._run()
        assert any(s <= 20.0 <= e for s, e in windows), "the rally is kept"
        assert not any(s <= 90.0 <= e for s, e in windows), "idle time is cut"

    def test_it_is_exactly_the_shared_helpers_at_the_weights_threshold(self):
        """No second preparation path: features → predict → windows, as the
        trainer scores them."""
        w = learned_weights(threshold=0.4)
        positions = _rally_track()
        probs = predict_in_play(compute_features(positions, [], self.DURATION, H), w)
        assert self._run(weights=w) == windows_from_probability(probs, 0.4, self.DURATION)

    def test_an_empty_verdict_is_a_plain_empty_list(self):
        """`[]` is a verdict the condense stage ships, so it must come back as
        one — not raised, and not an Abstained that would protect the old cut."""
        windows = self._run(weights=learned_weights(threshold=2.0))
        assert windows == [] and type(windows) is list

    def test_unvalidated_weights_are_refused_unless_allowed(self):
        w = learned_weights(validated=False)
        with pytest.raises(UnvalidatedWeightsError) as excinfo:
            self._run(weights=w)
        assert isinstance(excinfo.value, ValueError)
        assert self._run(weights=w, allow_unvalidated=True) == self._run()

    def test_a_missing_validated_key_counts_as_unvalidated(self):
        w = learned_weights()
        del w["validated"]
        with pytest.raises(UnvalidatedWeightsError):
            self._run(weights=w)

    @pytest.mark.parametrize("flag", ["false", "no", "true", 1, [True], None])
    def test_only_a_literal_true_counts_as_validated(self, flag):
        """The gate is `is True`: a string or int written by hand or by a tool
        is not a validation, whatever it says."""
        w = learned_weights()
        w["validated"] = flag
        with pytest.raises(UnvalidatedWeightsError):
            self._run(weights=w)

    @pytest.mark.parametrize("kw, match", [
        ({"frame_height": 0}, "frame height"),
        ({"duration": 0.0}, "duration"),
        ({"weights": learned_weights(feature_version=FEATURE_VERSION - 1)}, "retrain"),
        ({"weights": learned_weights(features=FEATURE_NAMES[:-1])}, "trained for features"),
        ({"weights": learned_weights(scaling={**SCALING, "rule": "global"})}, "scaled by"),
    ])
    def test_what_it_cannot_judge_raises(self, kw, match):
        with pytest.raises(ValueError, match=match):
            self._run(**kw)

    def test_weights_without_a_threshold_are_refused(self):
        w = learned_weights()
        del w["threshold"]
        with pytest.raises(ValueError, match="threshold"):
            self._run(weights=w)

    def test_an_empty_track_is_declined_not_scored(self):
        """Scored, an empty track is every column at its fill and comes back
        `[]` — a verdict, which would delete the previous condensed cut."""
        with pytest.raises(TrackTooSparseError):
            self._run(positions=[])

    def test_an_empty_track_is_declined_with_no_rate_floor(self):
        """At the default floor `track_is_usable` already rejects an empty
        track; at `min_track_rate=0.0` (no sparse gate) only the empty-track
        guard stands between it and a `[]` verdict."""
        with pytest.raises(TrackTooSparseError, match="no ball track"):
            self._run(positions=[], min_track_rate=0.0)

    def test_a_sparse_track_is_declined_on_guardeds_test(self):
        sparse = _rally_track(step=1.2)   # ~0.83 usable samples/s
        assert not dt.track_is_usable(dt.speed_samples(sparse, H), self.DURATION)
        with pytest.raises(TrackTooSparseError):
            self._run(positions=sparse)
        # The floor is a parameter: below it, the same track is judged.
        assert isinstance(self._run(positions=sparse, min_track_rate=0.5), list)

    def test_the_sparse_floor_defaults_to_guardeds(self):
        learned = inspect.signature(active_windows_learned).parameters["min_track_rate"].default
        guarded = inspect.signature(dt.active_windows_guarded).parameters["min_track_rate"].default
        assert learned == guarded

    def test_refusals_happen_before_any_compute(self, monkeypatch):
        """Raising on the cheap checks first is what keeps a refusal from
        costing a feature pass — and from being masked by a compute error."""
        import ml.pipeline.dead_time_ml as dml

        def boom(*a, **k):
            raise AssertionError("computed before refusing")

        monkeypatch.setattr(dml, "compute_features", boom)
        for kw, exc in [
            ({"frame_height": 0}, ValueError),
            ({"weights": learned_weights(validated=False)}, UnvalidatedWeightsError),
            ({"weights": learned_weights(feature_version=0)}, ValueError),
            ({"positions": []}, TrackTooSparseError),
        ]:
            with pytest.raises(exc):
                self._run(**kw)

    def test_one_summary_line(self, caplog):
        with caplog.at_level("INFO", logger="ml.pipeline.dead_time_ml"):
            windows = self._run()
        lines = [r for r in caplog.records if r.name == "ml.pipeline.dead_time_ml"]
        assert len(lines) == 1
        msg = lines[0].getMessage()
        assert "threshold 0.500" in msg and f"{len(windows)} windows" in msg

    def test_a_logging_failure_does_not_break_the_builder(self, monkeypatch):
        import ml.pipeline.dead_time_ml as dml

        def broken(*a, **k):
            raise RuntimeError("log handler down")

        expected = self._run()
        monkeypatch.setattr(dml.logger, "info", broken)
        assert self._run() == expected

    def test_the_committed_weights_run_when_allowed(self):
        weights = load_weights(WEIGHTS_PATH, warn_unvalidated=False)
        assert isinstance(
            self._run(weights=weights, allow_unvalidated=True), list)
        with pytest.raises(UnvalidatedWeightsError):
            self._run(weights=weights)

    # ── CF-396: the builder decodes by the weights' own decoder ──

    HMM = {"p_stay_play": 0.9, "p_stay_dead": 0.93, "prior_play": 0.35}

    def test_hmm_weights_are_decoded_by_the_hmm_not_by_threshold(self, monkeypatch):
        """The failure this guards is silent: hmm weights thresholded would
        still return plausible windows, just not the ones that were scored."""
        import ml.pipeline.dead_time_ml as dml

        w = learned_weights(decoder="hmm", hmm=dict(self.HMM))
        raw = predict_in_play(compute_features(_rally_track(), [], self.DURATION, H), w,
                              smooth=False)
        expected = viterbi_windows(raw, self.DURATION, **self.HMM)

        def boom(*a, **k):
            raise AssertionError("hmm weights went through windows_from_probability")

        monkeypatch.setattr(dml, "windows_from_probability", boom)
        assert self._run(weights=w) == expected
        assert any(s <= 20.0 <= e for s, e in expected), "the rally is kept"

    def test_hmm_weights_decode_the_raw_trace_with_the_records_stays(self, monkeypatch):
        """The clean rally above decodes the same with the stays swapped or the
        trace smoothed; `_noisy_trace` does not (R5-M1). The builder must ask
        for the unsmoothed trace and hand it, with the record, to the hmm."""
        import ml.pipeline.dead_time_ml as dml

        seen: list = []

        def fake_predict(features, weights, *, smooth=True):
            seen.append(smooth)
            return _noisy_trace()

        monkeypatch.setattr(dml, "predict_in_play", fake_predict)
        w = learned_weights(decoder="hmm", hmm=dict(FOLD_HMM))
        assert self._run(weights=w, duration=240.0,
                         positions=_rally_track(duration=240.0)) == NOISY_HMM_WINDOWS
        assert seen == [False]

    def test_an_explicit_threshold_decoder_matches_a_file_without_the_key(self):
        """Every file before CF-396 has no "decoder"; it must decode as before."""
        assert self._run(weights=learned_weights(decoder="threshold")) == self._run()

    @pytest.mark.parametrize("over, match", [
        ({"decoder": "crf"}, "decoder"),
        ({"decoder": "hmm"}, "hmm"),
        ({"decoder": "hmm", "hmm": {"p_stay_play": 0.9, "p_stay_dead": 0.9}}, "hmm"),
    ])
    def test_a_decoder_it_cannot_apply_is_refused_before_any_compute(
            self, monkeypatch, over, match):
        import ml.pipeline.dead_time_ml as dml

        def boom(*a, **k):
            raise AssertionError("computed before refusing")

        monkeypatch.setattr(dml, "compute_features", boom)
        with pytest.raises(ValueError, match=match):
            self._run(weights=learned_weights(**over))

    def test_one_summary_line_under_hmm(self, caplog):
        w = learned_weights(decoder="hmm", hmm=dict(self.HMM))
        with caplog.at_level("INFO", logger="ml.pipeline.dead_time_ml"):
            windows = self._run(weights=w)
        lines = [r for r in caplog.records if r.name == "ml.pipeline.dead_time_ml"]
        assert len(lines) == 1
        msg = lines[0].getMessage()
        assert "hmm" in msg and f"{len(windows)} windows" in msg


class TestTheEvalRung:
    """`v6` scores the learned builder on the ladder before CF-395 validates it."""

    def _game(self, positions):
        return dv.Game(test_id="t", video_file="t.mp4", duration=120.0, frame_height=H,
                       positions=positions, contacts=[], human_keep=[], raw={})

    def test_v6_is_on_the_ladder_and_labelled_in_sample(self):
        label, fn = dv.VARIANTS["v6"]
        assert fn is dv.v6_learned
        assert "IN-SAMPLE" in label and "learned" in label

    def test_the_shipping_rows_do_not_move(self):
        from ml.eval import visualize_deadtime as vd
        assert (vd.SHIPPING, vd.BASELINE, vd.LEARNED) == ("v5", "v0", "v6")

    def test_v6_scores_unvalidated_weights(self, monkeypatch, tmp_path):
        import ml.pipeline.dead_time_ml as dml

        p = tmp_path / "w.json"
        p.write_text(json.dumps(learned_weights(validated=False)))
        monkeypatch.setattr(dml, "WEIGHTS_PATH", p)
        positions = _rally_track()
        assert dv.v6_learned(self._game(positions)) == active_windows_learned(
            [], positions, 120.0, H, weights=learned_weights())

    def test_v6_reads_the_path_at_call_time(self, monkeypatch, tmp_path):
        """The loader caches per path, so repointing WEIGHTS_PATH is not
        answered from the previous file."""
        import ml.pipeline.dead_time_ml as dml

        a, b = tmp_path / "a.json", tmp_path / "b.json"
        a.write_text(json.dumps(learned_weights(threshold=0.5)))
        b.write_text(json.dumps(learned_weights(threshold=2.0)))
        game = self._game(_rally_track())
        monkeypatch.setattr(dml, "WEIGHTS_PATH", a)
        assert dv.v6_learned(game)
        monkeypatch.setattr(dml, "WEIGHTS_PATH", b)
        assert dv.v6_learned(game) == []

    def test_a_sparse_game_gets_productions_fallback(self):
        game = self._game(_rally_track(step=1.2))
        assert dv.v6_learned(game) == dv.v5_guarded(game)
        assert isinstance(dv.v6_learned(game), dt.Abstained)



class TestCommittedWeights:
    """The file CF-393 committed, read as the contract CF-394 will consume.

    Replaces the old `test_there_is_no_committed_weights_file_yet`, which pinned
    the docstring's "no weights file yet" — both changed together.
    """

    REQUIRED = ("features", "feature_version", "scaling", "context", "coef",
                "intercept", "threshold", "decoder", "trained_on", "git_commit", "folds",
                "context_variants", "validated")
    FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures"

    @pytest.fixture
    def weights(self):
        from ml.pipeline.dead_time_ml import WEIGHTS_PATH

        return load_weights(WEIGHTS_PATH)

    def test_it_loads_unvalidated_at_the_current_version(self, weights):
        """Committing is not enabling: CF-395 is the only thing that may flip it."""
        assert weights["validated"] is False
        assert weights["feature_version"] == FEATURE_VERSION

    def test_every_required_key_is_present(self, weights):
        missing = [k for k in self.REQUIRED if k not in weights]
        assert not missing
        assert "trained_at" not in weights, "a timestamp breaks byte-identical reruns"

    def test_the_coefficients_fit_the_recorded_context(self, weights):
        lags = weights["context"]["lags"]
        assert len(weights["coef"]) == len(FEATURE_NAMES) * (1 + len(lags))
        assert weights["context"] == context_rule(lags)
        assert weights["scaling"] == SCALING

    def test_the_folds_cover_all_five_fixtures(self, weights):
        ids = ["test1", "test2", "test3", "test4", "test5"]
        assert [f["held_out"] for f in weights["folds"]] == ids
        for variant in weights["context_variants"].values():
            assert [f["held_out"] for f in variant["folds"]] == ids

    def test_the_top_level_folds_are_the_shipped_variants(self, weights):
        """R3-N6: the chosen variant's folds are stored twice; they must agree."""
        chosen = [v for v in weights["context_variants"].values()
                  if v["context"] == weights["context"]]
        assert len(chosen) == 1
        assert chosen[0]["folds"] == weights["folds"]

    def test_the_shipped_threshold_is_the_median_fold_threshold(self, weights):
        assert weights["threshold"] == float(np.median(
            [f["threshold"] for f in weights["folds"]]))

    def test_the_trained_on_md5s_are_the_fixtures(self, weights):
        for entry in weights["trained_on"]:
            fx = json.loads(
                (self.FIXTURES / f"{entry['test_id']}_deadtime.json").read_text())
            assert entry["source_video_md5"] == fx["source_video_md5"]

    def test_it_names_a_decoder_the_code_knows(self, weights):
        """CF-396: written explicitly, so the file says how it is decoded."""
        assert weights["decoder"] in DECODERS
        assert ("hmm" in weights) == (weights["decoder"] == "hmm")

    def test_it_runs_through_predict_in_play(self, weights):
        feats = compute_features(track([t * 0.5 for t in range(40)], step=10.0),
                                 [{"time": 5.0}], 30.0, H)
        probs = predict_in_play(feats, weights)
        assert probs.shape == (30,)
        assert np.all((probs >= 0.0) & (probs <= 1.0))


class TestRobustScale:
    def test_median_and_p10_p90_range(self):
        col_a = np.arange(11, dtype=np.float64)        # median 5, p10 1, p90 9
        col_b = np.arange(11, dtype=np.float64) * 2.0  # median 10, range 16
        out = robust_scale(np.column_stack([col_a, col_b]))
        np.testing.assert_allclose(out[:, 0], (col_a - 5.0) / 8.0)
        np.testing.assert_allclose(out[:, 1], (col_b - 10.0) / 16.0)

    def test_a_constant_column_is_divided_by_one(self):
        X = np.column_stack([np.full(5, 3.0), np.arange(5.0)])
        out = robust_scale(X)
        assert np.all(np.isfinite(out))
        np.testing.assert_array_equal(out[:, 0], np.zeros(5))

    def test_a_saturated_column_falls_back_to_its_full_range(self):
        """
        p10 == p90 without the column being constant (test4's `track_rate_2s`
        sits at 3.0 bar a few rows): it is scaled by max - min, not left raw.
        """
        col = np.full(40, 3.0)
        col[0] = 1.25
        out = robust_scale(np.column_stack([col, np.arange(40.0)]))
        np.testing.assert_allclose(out[:, 0], (col - 3.0) / 1.75)
        assert out[0, 0] == pytest.approx(-1.0)

    def test_a_sparse_column_falls_back_to_its_full_range(self):
        """test3's `contacts_5s`: mostly zero, so p10 == p90 == 0."""
        col = np.zeros(30)
        col[[3, 11]] = 2.0
        out = robust_scale(col[:, None])
        np.testing.assert_allclose(out[:, 0], col / 2.0)

    def test_scaling_is_per_video(self):
        """The same game shifted and stretched scales to the same rows."""
        X = np.random.default_rng(0).normal(size=(50, 3))
        np.testing.assert_allclose(robust_scale(X), robust_scale(X * 4.0 + 7.0))


class TestAddContext:
    X = np.array([[0.0, 10.0], [1.0, 11.0], [2.0, 12.0], [3.0, 13.0]])

    def test_negative_lags_read_the_past_and_edges_replicate(self):
        out = add_context(self.X, [-1, 2])
        assert out.shape == (4, 6)
        np.testing.assert_array_equal(out[:, :2], self.X)
        # lag -1: row t carries t-1; row 0 replicates itself
        np.testing.assert_array_equal(out[:, 2], [0.0, 0.0, 1.0, 2.0])
        # lag +2: row t carries t+2; the last two rows replicate row 3
        np.testing.assert_array_equal(out[:, 4], [2.0, 3.0, 3.0, 3.0])
        np.testing.assert_array_equal(out[:, 5], [12.0, 13.0, 13.0, 13.0])

    def test_no_lags_is_the_input(self):
        np.testing.assert_array_equal(add_context(self.X, []), self.X)

    def test_the_default_lags_are_one_three_five_both_ways(self):
        assert CONTEXT_LAGS == (-5, -3, -1, 1, 3, 5)
        assert add_context(self.X).shape == (4, 2 * 7)


class TestPredictInPlay:
    def test_a_coef_of_the_wrong_width_is_refused(self):
        feats = np.zeros((10, len(FEATURE_NAMES)))
        w = {"coef": [0.0] * len(FEATURE_NAMES), "intercept": 0.0,
             "scaling": SCALING, "context": context_rule([-1, 1])}
        with pytest.raises(ValueError, match="coefficients"):
            predict_in_play(feats, w)

    def test_weights_scaled_another_way_are_refused(self):
        """R3-M1: a dict that bypassed `load_weights` is checked here too."""
        feats = np.zeros((10, len(FEATURE_NAMES)))
        w = {"coef": [0.0] * len(FEATURE_NAMES), "intercept": 0.0,
             "scaling": {**SCALING, "zero_range": "divide_by_1"},
             "context": context_rule([])}
        with pytest.raises(ValueError, match="scaled by"):
            predict_in_play(feats, w)
        w = {**w, "scaling": SCALING, "context": {"lags": []}}
        with pytest.raises(ValueError, match="context"):
            predict_in_play(feats, w)

    def test_unsmoothed_is_the_sigmoid_of_the_scaled_matrix(self):
        feats = np.random.default_rng(1).normal(size=(20, len(FEATURE_NAMES)))
        coef = np.linspace(-1, 1, len(FEATURE_NAMES))
        w = {"coef": coef.tolist(), "intercept": 0.25, "scaling": SCALING,
             "context": context_rule([])}
        expected = 1 / (1 + np.exp(-(robust_scale(feats) @ coef + 0.25)))
        np.testing.assert_allclose(predict_in_play(feats, w, smooth=False), expected)
        assert predict_in_play(feats, w).shape == (20,)

    @pytest.mark.parametrize("n", [1, 2, 4, 5])
    def test_a_video_shorter_than_the_smoothing_kernel_keeps_its_length(self, n):
        feats = np.random.default_rng(2).normal(size=(n, len(FEATURE_NAMES)))
        w = {"coef": [0.1] * len(FEATURE_NAMES), "intercept": 0.0,
             "scaling": SCALING, "context": context_rule([])}
        assert predict_in_play(feats, w).shape == (n,)
        assert smooth_probability(np.ones(n)).shape == (n,)

    def test_a_short_trace_is_smoothed_about_its_own_rows(self):
        # each row averages rows t-2..t+2 over a window of 5, zero-padded
        np.testing.assert_allclose(smooth_probability(np.array([1.0, 2.0, 3.0])),
                                   [1.2, 1.2, 1.2])

    def test_smoothing_matches_convolve_same_from_the_kernel_length_up(self):
        probs = np.random.default_rng(3).random(30)
        for n in (5, 6, 30):
            np.testing.assert_allclose(
                smooth_probability(probs[:n]),
                np.convolve(probs[:n], np.ones(5) / 5, mode="same"))


class TestWindowsFromProbability:
    def test_runs_are_padded(self):
        probs = np.zeros(60)
        probs[20:25] = 0.9      # seconds [20, 25)
        assert windows_from_probability(probs, 0.5, 60.0) == [(17.0, 27.5)]

    def test_close_runs_merge_and_far_ones_do_not(self):
        probs = np.zeros(100)
        probs[10:12] = probs[18:20] = 0.9   # padded (7, 14.5) and (15, 22.5): 0.5s apart, merge
        probs[60:62] = 0.9                  # padded (57, 64.5): far from (7, 22.5)
        assert windows_from_probability(probs, 0.5, 100.0) == [(7.0, 22.5), (57.0, 64.5)]

    def test_windows_are_clamped_to_the_video(self):
        probs = np.zeros(10)
        probs[:2] = probs[-1] = 0.9
        out = windows_from_probability(probs, 0.5, 9.4, merge_gap_seconds=0.0)
        assert out == [(0.0, 4.5), (6.0, 9.4)]

    def test_the_threshold_is_inclusive_and_empty_input_gives_nothing(self):
        assert windows_from_probability(np.array([0.5]), 0.5, 1.0) == [(0.0, 1.0)]
        assert windows_from_probability(np.array([0.49]), 0.5, 1.0) == []
        assert windows_from_probability(np.array([]), 0.5, 10.0) == []
        assert windows_from_probability(np.array([0.9]), 0.5, 0.0) == []


# CF-396. Transitions of the size the fixtures' folds fit (the trainer prints
# them: p_stay_play ≈ 0.86–0.89, p_stay_dead ≈ 0.94, prior ≈ 0.31–0.34).
FOLD_HMM = {"p_stay_play": 0.88, "p_stay_dead": 0.94, "prior_play": 0.33}
NO_PAD = {"pad_before": 0.0, "pad_after": 0.0, "merge_gap_seconds": 0.0}


def _noisy_trace() -> np.ndarray:
    """
    240s whose hmm windows depend on which stay is which and on the trace
    being raw — the clean step traces above decode the same either way:

      - a 100s lull alternating 0.35/0.65 between two 20s rallies. Its mean
        log-odds is 0, so the stays alone decide it: under FOLD_HMM dead is the
        cheaper state to remain in and the lull is cut; with the stays swapped
        play is, and the two rallies become one window;
      - three 3s bursts at 0.99 in dead. Raw, each is a short rally; a 5s
        moving average flattens them to ~0.6 at the peak and they vanish.
    """
    burst = np.r_[np.full(17, 0.05), np.full(3, 0.99)]
    return np.r_[np.full(20, 0.05), np.full(20, 0.9), np.tile([0.35, 0.65], 50),
                 np.full(20, 0.9), np.tile(burst, 3), np.full(20, 0.05)]


# FOLD_HMM's windows on _noisy_trace, default pads and merge gap.
NOISY_HMM_WINDOWS = [(17.0, 42.5), (136.0, 162.5), (174.0, 182.5),
                     (194.0, 202.5), (214.0, 222.5)]


class TestViterbi:
    def _rally_with_dip(self, dip_len: int, dip_p: float) -> np.ndarray:
        """20s dead at 0.1, a 20s rally at 0.9 with `dip_len` seconds at
        `dip_p` in its middle, 20s dead at 0.1."""
        probs = np.r_[np.full(20, 0.1), np.full(20, 0.9), np.full(20, 0.1)]
        probs[30:30 + dip_len] = dip_p
        return probs

    def test_a_one_second_dip_inside_a_rally_decodes_as_one_window(self):
        """The ticket's acceptance case. With no pad and no merge gap nothing
        else can bridge the dip, and thresholding the same trace splits it."""
        probs = self._rally_with_dip(1, 0.2)
        assert windows_from_probability(probs, 0.5, 60.0, **NO_PAD) == [
            (20.0, 30.0), (31.0, 40.0)], "the dip is deep enough to split a threshold"
        path = viterbi_path(probs, **FOLD_HMM)
        assert path[20:40].all() and not path[:20].any() and not path[40:].any()
        assert viterbi_windows(probs, 60.0, **FOLD_HMM, **NO_PAD) == [(20.0, 40.0)]

    def test_a_long_dip_still_splits_the_rally(self):
        """The duration prior bridges a second, not a dead gap: ten seconds
        of near-certain dead is two rallies."""
        probs = np.r_[np.full(20, 0.1), np.full(10, 0.9), np.full(10, 0.05),
                      np.full(10, 0.9), np.full(20, 0.1)]
        assert viterbi_windows(probs, 70.0, **FOLD_HMM, **NO_PAD) == [
            (20.0, 30.0), (40.0, 50.0)]

    @pytest.mark.parametrize("play, dead, expected", [(14, 6, True), (6, 14, False)])
    def test_a_flat_half_trace_follows_the_prior(self, play, dead, expected):
        """
        Uninformative emissions, both directions, with the prior and the stay
        probabilities fitted together from labels (in-play fraction 0.7, then
        0.3), as the trainer fits them: the path starts in the likelier state
        and the stays hold it there.

        Fitted together they agree — the transitions' stationary share of play
        is the prior when runs alternate, as here. Set by hand they need not: prior 0.7 with dead runs
        longer than play runs sends a long flat trace to dead after the first
        second, because the prior is only the initial distribution and staying
        dead is the cheaper path from there.
        """
        y = np.array(([0] * dead + [1] * play) * 5, dtype=float)
        hmm = fit_transitions([y])
        assert hmm["prior_play"] == round(play / (play + dead), 6)
        path = viterbi_path(np.full(200, 0.5), **hmm)
        assert path.tolist() == [expected] * 200

    @pytest.mark.parametrize("prior, expected", [(0.7, True), (0.3, False)])
    def test_with_equal_stays_the_prior_alone_decides_a_flat_trace(self, prior, expected):
        """Equal stays make neither state cheaper to remain in, so the first
        second's distribution — the prior — is the whole decision."""
        path = viterbi_path(np.full(200, 0.5), p_stay_play=0.9, p_stay_dead=0.9,
                            prior_play=prior)
        assert path.tolist() == [expected] * 200

    def test_ties_go_to_dead(self):
        """Flat 0.5, equal stays, a 0.5 prior: every path scores the same, and
        the docstring's rule picks dead."""
        path = viterbi_path(np.full(20, 0.5), p_stay_play=0.9, p_stay_dead=0.9,
                            prior_play=0.5)
        assert path.tolist() == [False] * 20

    def test_entering_play_costs_one_minus_the_dead_stay(self):
        """A trace that ends on 7s at 0.6 after dead: entering play there pays
        log(1 − p_stay_dead) = log 0.06 once, more than those seconds gain, so
        they stay dead. Read the other way round (log 0.12) the entry would be
        cheap enough. Interior runs pay one entry and one exit either way, so
        only an unmatched entry like this one tells them apart."""
        probs = np.r_[np.full(20, 0.05), np.full(7, 0.6)]
        assert not viterbi_path(probs, **FOLD_HMM).any()

    def test_the_prior_is_only_the_initial_distribution(self):
        """Applied to every second, a 0.99 prior (log-odds 4.6) would outvote a
        0.05 trace (log-odds 2.9) everywhere and keep it all. As the first
        second's distribution it cannot."""
        path = viterbi_path(np.full(50, 0.05), p_stay_play=0.88, p_stay_dead=0.94,
                            prior_play=0.99)
        assert not path[1:].any()

    def test_it_reads_raw_probabilities_not_a_smoothed_trace(self):
        """A dip smoothing would hide, held for long enough, is still cut."""
        probs = self._rally_with_dip(6, 0.02)
        path = viterbi_path(probs, **FOLD_HMM)
        assert not path[30:36].any()
        assert smooth_probability(probs)[30:36].max() > 0.2, \
            "smoothing alone would have blurred it"

    def test_exact_zero_and_one_are_floored_not_infinite(self):
        probs = np.r_[np.zeros(10), np.ones(10), np.zeros(10)]
        path = viterbi_path(probs, **FOLD_HMM)
        assert path.tolist() == [False] * 10 + [True] * 10 + [False] * 10

    @pytest.mark.parametrize("stay, held", [(0.9995, True), (0.995, False)])
    def test_one_saturated_second_costs_the_floor_not_infinity(self, stay, held):
        """
        One exact 0.0 inside a confident rally, and one exact 1.0 inside dead.
        Floored at 1e-6, the disagreeing second costs log(1e-6) = -13.8, so it
        is outvoted when leaving and re-entering costs more (2·log(0.0005) =
        -15.2) and splits the run when that is cheaper (2·log(0.005) = -10.6).
        Unfloored, log(0) is -inf and it would split the run at any stay.
        """
        hmm = {"p_stay_play": stay, "p_stay_dead": stay, "prior_play": 0.5}
        rally = np.full(30, 0.9)
        rally[15] = 0.0
        dead = np.full(30, 0.1)
        dead[15] = 1.0
        path = viterbi_path(np.r_[rally, dead], **hmm)
        assert path[15] == held and path[45] == (not held)
        assert path[:15].all() and path[16:30].all()
        assert not path[30:45].any() and not path[46:].any()

    def test_empty_and_zero_duration(self):
        assert viterbi_path(np.array([]), **FOLD_HMM).shape == (0,)
        assert viterbi_windows(np.array([]), 10.0, **FOLD_HMM) == []
        assert viterbi_windows(np.array([0.9]), 0.0, **FOLD_HMM) == []

    def test_it_shares_the_threshold_decoders_pad_and_merge_tail(self):
        """Same flags in, same windows out: the two decoders differ only in how
        they decide which seconds are play."""
        probs = np.r_[np.full(10, 0.02), np.full(8, 0.98), np.full(30, 0.02),
                      np.full(8, 0.98), np.full(10, 0.02)]
        path = viterbi_path(probs, **FOLD_HMM)
        assert viterbi_windows(probs, 66.0, **FOLD_HMM) == windows_from_probability(
            path.astype(float), 0.5, 66.0)


class TestFitTransitions:
    def test_stay_is_one_minus_one_over_the_mean_run(self):
        y = np.array([0] * 10 + [1] * 5 + [0] * 10 + [1] * 5, dtype=float)
        assert fit_transitions([y]) == {
            "p_stay_play": 0.8, "p_stay_dead": 0.9, "prior_play": round(10 / 30, 6)}

    def test_runs_do_not_cross_a_game_boundary(self):
        """Game A ends in play and B starts in play: two 2s runs, not one 4s."""
        a = np.array([0, 0, 1, 1], dtype=float)
        b = np.array([1, 1, 0, 0], dtype=float)
        assert fit_transitions([a, b])["p_stay_play"] == 0.5
        assert fit_transitions([np.r_[a, b]])["p_stay_play"] == 0.75

    def test_runs_are_pooled_across_games_not_averaged_per_game(self):
        a = np.array([0] * 4 + [1] * 2 + [0] * 4 + [1] * 2, dtype=float)   # play runs 2, 2
        b = np.array([0] * 4 + [1] * 10, dtype=float)                       # play run 10
        # pooled mean (2+2+10)/3 = 4.667 → 0.785714; per-game means 2 and 10 → 0.7
        assert fit_transitions([a, b])["p_stay_play"] == round(1 - 3 / 14, 6)

    def test_stay_and_prior_are_clamped(self):
        flicker = np.array([0, 1] * 20, dtype=float)       # runs of 1 → raw stay 0
        assert fit_transitions([flicker])["p_stay_play"] == 0.5
        long_dead = np.r_[np.zeros(5000), np.ones(3)]     # raw stay 0.9998
        fitted = fit_transitions([long_dead])
        assert fitted["p_stay_dead"] == 0.999
        assert fitted["prior_play"] == 0.01               # 3/5003 floored

    def test_a_state_that_never_occurs_cannot_be_fitted(self):
        with pytest.raises(ValueError, match="play"):
            fit_transitions([np.zeros(30)])
        with pytest.raises(ValueError, match="play"):
            fit_transitions([np.ones(30)])


class TestDecoderContract:
    @staticmethod
    def _weights(**over):
        return learned_weights(**over)

    def test_the_decoders(self):
        assert DECODERS == ("threshold", "hmm")

    def test_a_missing_decoder_is_threshold(self):
        raw = np.r_[np.full(10, 0.1), np.full(10, 0.9), np.full(10, 0.1)]
        w = self._weights(threshold=0.5)
        check_contract(w)
        assert decode_windows(raw, w, 30.0) == windows_from_probability(
            smooth_probability(raw), 0.5, 30.0)

    def test_hmm_decodes_the_raw_trace_with_the_record(self):
        raw = np.r_[np.full(10, 0.1), np.full(10, 0.9), np.full(10, 0.1)]
        w = self._weights(decoder="hmm", hmm=dict(FOLD_HMM))
        assert decode_windows(raw, w, 30.0) == viterbi_windows(raw, 30.0, **FOLD_HMM)

    def test_hmm_decodes_a_noisy_trace_raw_with_each_stay_in_its_place(self):
        """On a trace where the answer depends on them: the record's stays in
        their own places, applied to the raw trace (R5-M1)."""
        raw = _noisy_trace()
        w = self._weights(decoder="hmm", hmm=dict(FOLD_HMM))
        swapped = {**FOLD_HMM, "p_stay_play": FOLD_HMM["p_stay_dead"],
                   "p_stay_dead": FOLD_HMM["p_stay_play"]}
        assert viterbi_windows(raw, 240.0, **FOLD_HMM) == NOISY_HMM_WINDOWS
        assert viterbi_windows(raw, 240.0, **swapped) != NOISY_HMM_WINDOWS, \
            "the trace must tell the stays apart"
        assert viterbi_windows(smooth_probability(raw), 240.0, **FOLD_HMM) \
            != NOISY_HMM_WINDOWS, "the trace must tell raw from smoothed"
        assert decode_windows(raw, w, 240.0) == NOISY_HMM_WINDOWS

    @pytest.mark.parametrize("prior, expected", [(0.7, [(0.0, 30.0)]), (0.3, [])])
    def test_hmm_decodes_with_the_records_prior(self, prior, expected):
        """With equal stays a flat 0.5 trace is decided by the prior alone
        (see TestViterbi), so a prior dropped or defaulted on the way to the
        decoder changes the windows."""
        hmm = {"p_stay_play": 0.9, "p_stay_dead": 0.9, "prior_play": prior}
        w = self._weights(decoder="hmm", hmm=hmm)
        assert decode_windows(np.full(30, 0.5), w, 30.0) == expected

    @pytest.mark.parametrize("over, match", [
        ({"decoder": "crf"}, "decoder 'crf'"),
        ({"decoder": None}, "decoder None"),
        ({"decoder": "hmm"}, "record None"),
        ({"decoder": "hmm", "hmm": {"p_stay_play": 0.9, "p_stay_dead": 0.9}}, "expected keys"),
        ({"decoder": "hmm", "hmm": {**FOLD_HMM, "extra": 0.5}}, "expected keys"),
        ({"decoder": "hmm", "hmm": {**FOLD_HMM, "p_stay_play": 1.0}}, "p_stay_play"),
        ({"decoder": "hmm", "hmm": {**FOLD_HMM, "p_stay_dead": 0.0}}, "p_stay_dead"),
        ({"decoder": "hmm", "hmm": {**FOLD_HMM, "prior_play": True}}, "prior_play"),
        ({"decoder": "hmm", "hmm": {**FOLD_HMM, "prior_play": "0.3"}}, "prior_play"),
    ])
    def test_what_it_cannot_decode_is_refused(self, tmp_path, over, match):
        """By check_contract, so by load_weights and decode_windows alike."""
        w = self._weights(**over)
        with pytest.raises(ValueError, match=match):
            check_contract(w)
        with pytest.raises(ValueError, match=match):
            decode_windows(np.full(5, 0.5), w, 5.0)
        p = tmp_path / "w.json"
        p.write_text(json.dumps(w))
        with pytest.raises(ValueError, match=match):
            load_weights(p)

    def test_a_valid_hmm_file_loads(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_text(json.dumps(self._weights(decoder="hmm", hmm=dict(FOLD_HMM))))
        assert load_weights(p)["hmm"] == FOLD_HMM


class TestGuards:
    def test_a_non_positive_frame_height_is_refused(self):
        with pytest.raises(ValueError, match="frame_height"):
            compute_features(track([1.0]), [], 5.0, 0)

    def test_pixel_columns_are_frame_relative(self):
        """The same motion at two resolutions gives the same features."""
        small = compute_features(track([0.5, 1.0, 1.5], y=90.0, step=36.0), [], 3.0, 360)
        large = compute_features(track([0.5, 1.0, 1.5], y=270.0, step=108.0), [], 3.0, 1080)
        np.testing.assert_allclose(small, large)


# Every column's fill, and which way it votes. The table is the contract: a
# change to any fill fails here, not just the two a round happened to mutate.
#
# "dead" means the fill sits at the end of the column's range that argues
# AGAINST in-play; "neutral" means it commits to neither. Nothing may vote
# "play" — a second with no evidence must never be the strongest in-play signal
# in the matrix, which is the defect this table exists to make unshippable.
EXPECTED_FILLS = [
    ("track_rate_2s",      0.0,             "dead"),     # no samples
    ("track_coverage_10s", 0.0,             "dead"),     # no covered bins
    ("mean_speed_3s",      0.0,             "dead"),     # no motion
    ("fast_fraction_5s",   0.0,             "dead"),     # nothing fast
    ("contacts_5s",        0.0,             "dead"),     # no contacts
    ("contacts_15s",       0.0,             "dead"),
    ("since_contact",      60.0,            "dead"),     # saturated = long ago
    ("until_contact",      60.0,            "dead"),
    ("mean_conf_3s",       0.0,             "dead"),     # nothing detected
    ("y_std_5s",           0.0,             "dead"),     # no spread
    ("mean_y_5s",          EMPTY_MEAN_Y,    "neutral"),  # frame centre; see below
    ("max_conf_3s",        0.0,             "dead"),
    ("max_speed_3s",       0.0,             "dead"),
]


class TestEveryFill:
    def test_the_table_covers_every_column_in_order(self):
        assert [name for name, _, _ in EXPECTED_FILLS] == FEATURE_NAMES

    def test_the_fills_are_literals_not_the_constants_they_pin(self):
        """Why 60.0 is written out above rather than CONTACT_GAP_CAP.

        Spelling the expectation as the symbol makes it follow the code: halving
        CONTACT_GAP_CAP moved both sides and the whole suite stayed green. The
        literal is the point — it is an independent record of what the value is
        supposed to be.
        """
        assert CONTACT_GAP_CAP == 60.0
        assert EMPTY_MEAN_Y == 0.5

    @pytest.mark.parametrize("name,value,vote", EXPECTED_FILLS, ids=[f[0] for f in EXPECTED_FILLS])
    def test_an_evidence_free_second_takes_the_documented_fill(self, name, value, vote):
        """All 13, not a sample of them.

        A previous version pinned two fills and the commit message described the
        result as covering the class; `mean_conf_3s`'s fill could be inverted to
        1.0 and `CONTACT_GAP_CAP` halved with the whole suite green.
        """
        f = compute_features([], [], 4.0, H)
        assert (f[:, col(name)] == value).all(), f"{name} no longer fills at {value}"

    @pytest.mark.parametrize(
        "name,value,vote", [t for t in EXPECTED_FILLS if t[2] == "dead"],
        ids=[t[0] for t in EXPECTED_FILLS if t[2] == "dead"],
    )
    def test_a_dead_voting_fill_is_not_beaten_by_real_evidence_in_the_dead_direction(
        self, name, value, vote
    ):
        """The fill must be AT the dead end, not merely near it.

        Pinning the value alone does not say which way it points; this asserts
        that no real track produces a value further toward 'dead' than the fill.

        **The `"dead"` label is a ONE-WAY RATCHET, and both previous versions of
        this paragraph got it wrong in opposite directions.** The first claimed
        the label was "a property rather than a comment"; the correction said it
        was "a comment". Measured, both directions:

            mean_conf_3s  "dead" -> "play"   402 passed — one fewer assertion,
                                             suite green, nobody notices
            mean_y_5s  "neutral" -> "dead"   1 failed — `column.min() >= value`
                                             on a high-ball track, 0.25 < 0.5

        So ADDING a wrong `dead` tag fails; REMOVING a right one is free, because
        the label is this test's parametrize selector and an untagged column
        simply leaves the list. A guard that cannot lose a case it already has
        is not the same as one that cannot be emptied.

        The table's other two columns are not soft: `test_the_table_covers_every
        _column_in_order` pins the names against `FEATURE_NAMES`, and the value
        is what this test asserts. Only `vote` is. Replacing the scaffolding with
        a golden-output pin over all thirteen columns is CF-419 (#551), which
        needs no prose about its own strength — which is the argument for it,
        given that this paragraph has now been wrong twice.
        """
        busy = compute_features(
            track([t / 2 for t in range(0, 21)], y=90.0, step=60.0),
            [{"time": 5.0}], 10.0, H,
        )
        column = busy[:, col(name)]
        if name in ("since_contact", "until_contact"):
            assert column.max() <= value, f"{name} exceeds its saturated fill"
        else:
            assert column.min() >= value, f"{name} goes below its fill on real data"

    def test_the_neutral_fill_is_bracketed_by_real_evidence_on_both_sides(self):
        """`mean_y_5s` is the one column whose signal is inverted — smaller y is
        higher in frame, so more in-play. Its fill must sit BETWEEN a real high
        ball and a real low one, or it is voting rather than abstaining."""
        high = compute_features(track([1.0, 1.5, 2.0], y=30.0), [], 4.0, H)
        low = compute_features(track([1.0, 1.5, 2.0], y=330.0), [], 4.0, H)
        assert high[1, col("mean_y_5s")] < EMPTY_MEAN_Y < low[1, col("mean_y_5s")]


class TestNaNConventions:
    """The PR's most-argued decision, which nothing pinned.

    `dead_time.py` has two conventions for an over-ceiling (NaN) speed sample.
    `motion_anchor_windows` counts it in the fast-fraction DENOMINATOR, so it
    votes not-fast; `speed_gate_contacts` masks it out entirely. This module
    uses the anchor's for `fast_fraction_5s` and the masked one for the speed
    MAGNITUDE columns, and the difference is only visible on a MIXED window —
    an all-NaN window gives 0.0 either way, which is why the existing
    all-over-ceiling test cannot separate them.
    """

    @staticmethod
    def _mixed():
        # 20 samples at a believable 0.56 fh/s, with one unbelievable hop.
        pos = track([t / 2 for t in range(0, 21)], x=0.0, y=180.0, step=100.0)
        pos[10]["x"] = 99999.0
        return pos

    def test_the_fixture_really_is_mixed(self):
        _, speeds = dt.speed_samples(self._mixed(), H)
        assert np.isnan(speeds).any(), "no NaN — the conventions cannot differ here"
        assert np.isfinite(speeds).any(), "all NaN — the conventions agree here"

    def test_fast_fraction_counts_the_unbelievable_sample_as_not_fast(self):
        """The anchor's convention. Under the masked convention the NaN would
        leave the denominator and the fraction would rise to 1.0; under this one
        it stays in, so the fraction is strictly below 1.0 while every
        believable sample in the window is fast."""
        f = compute_features(self._mixed(), [], 10.0, H)
        frac = f[:, col("fast_fraction_5s")]
        _, speeds = dt.speed_samples(self._mixed(), H)
        believable = speeds[np.isfinite(speeds)]
        assert (believable >= FAST_SPEED_FH).all(), "fixture's real samples are not all fast"
        assert frac.max() < 1.0, (
            "fast_fraction_5s reached 1.0, so the NaN left the denominator — that "
            "is speed_gate_contacts' convention, not motion_anchor_windows'"
        )
        assert frac.max() > 0.0

    def test_the_speed_magnitudes_mask_it_instead(self):
        """The other convention, on the columns that need it: an unjudgeable
        magnitude cannot be averaged, so it is excluded rather than counted.

        Asserted on a ROW WHOSE WINDOW CONTAINS THE NaN, not on the column max.
        The max is bit-identical under both conventions — rows at the ends have
        NaN-free ±3s windows and supply it — so the previous version of this
        test passed with the mean using the counted denominator, which reads
        ~18% low on six of ten rows. `.max()` over a column is almost always
        the wrong reduction for a windowed property: it finds the row the
        mutation did not touch.
        """
        pos = self._mixed()
        f = compute_features(pos, [], 10.0, H)
        sp_times, speeds = dt.speed_samples(pos, H)
        believable = speeds[np.isfinite(speeds)]

        # Row 5's ±3s window straddles the bad hop at t=5.0.
        row = 5
        lo, hi = row + 0.5 - 3.0, row + 0.5 + 3.0
        inside = (sp_times >= lo) & (sp_times < hi)
        assert np.isnan(speeds[inside]).any(), "row 5's window no longer holds the NaN"
        expected = speeds[inside][np.isfinite(speeds[inside])].mean()

        assert f[row, col("mean_speed_3s")] == pytest.approx(expected), (
            "mean_speed_3s is not the mean of the BELIEVABLE samples in its "
            "window — a counted denominator would divide by one more"
        )
        assert f[row, col("max_speed_3s")] == pytest.approx(
            speeds[inside][np.isfinite(speeds[inside])].max()
        )
        # And the column as a whole still reports the believable maximum.
        assert f[:, col("max_speed_3s")].max() == pytest.approx(believable.max())

    def test_y_std_is_positive_when_the_ball_actually_moves_vertically(self):
        """`y_std_5s` had no positive-value coverage at all.

        Flipping the variance to `mean_y**2 - mean_y2` lets the `np.maximum(...,
        0)` clamp drive the column to a constant 0.0 on every input, and the
        whole suite passed: the only other fixtures touching it use a
        constant-y track (std 0 either way) and the two fill rows expect 0. One
        of thirteen features would have become a dead constant that CF-393
        trains on.
        """
        oscillating = [
            {"time": t / 2, "x": 0.0, "y": 60.0 if (t // 2) % 2 else 300.0,
             "confidence": 0.9}
            for t in range(0, 21)
        ]
        f = compute_features(oscillating, [], 10.0, H)
        spread = f[:, col("y_std_5s")]
        # The derived value, not a threshold. y alternates 60 and 300 of 360, so
        # a window holding equal counts has std (0.8333-0.1667)/2 = 0.3333 and
        # VARIANCE 0.1111 — pinning the number distinguishes the two, where
        # `> 0.1` accepts either and lets the sqrt be dropped.
        assert spread.max() == pytest.approx(0.3333, abs=1e-3), (
            "y_std_5s is not the standard deviation — 0.1111 means the sqrt is gone"
        )

        # And a ball that genuinely does not move vertically still reads ~0, so
        # the assertion above is measuring spread rather than merely non-zero.
        flat = compute_features(track([t / 2 for t in range(0, 21)], y=180.0), [], 10.0, H)
        assert flat[:, col("y_std_5s")].max() == pytest.approx(0.0, abs=1e-12)


class TestProducersForwardConfidence:
    """Every producer of the position-dict shape must forward `confidence`.

    This is a source-level guard on purpose. The columns it protects fail
    SILENTLY: `compute_features` reads `p.get("confidence", 0.0)`, and 0.0 is
    also mean_conf_3s' and max_conf_3s' documented empty-window fill — so a
    producer that stops forwarding the field produces a matrix that is finite,
    plausible, and constant on two of thirteen columns. Nothing raises, no
    shape changes, and the trainer (CF-393) would fit those coefficients
    against a dead column. That is the CF-420 defect exactly, and it survived
    until a review round went looking at the producers.

    Present is not enough: a producer that writes `"confidence": 1.0` carries
    the key and still yields a constant column, on invented data rather than
    a dead one. So where the dict is a literal, the value must READ the
    source's field (`p.confidence` / `p["confidence"]`) — not a constant, and
    not a `.get` with a default that invents one. tune_contacts' key-tuple
    comprehension has no value expression to inspect, so its value is pinned
    behaviourally in TestLoadersForwardTheSourceValue below.

    Parsed rather than grepped, per CF-279: a regex over these lines loses to
    reformatting.
    """

    ROOT = Path(__file__).resolve().parents[2]
    PRODUCERS = (
        "api/app/workers/tasks.py",
        "ml/eval/harness.py",
        "ml/eval/diagnose_detection.py",
        "ml/eval/deadtime_variants.py",
        "ml/eval/tune_contacts.py",
    )

    @staticmethod
    def _key_sets(path):
        """Every literal listing of position-dict keys in the file, as
        (keys, the value node for "confidence" or None).

        Two spellings, because the producers genuinely use both: a dict literal
        (`{"time": ..., "x": ...}`) in four of them, and a key tuple driving a
        comprehension (`{k: p[k] for k in ("time", ...) if k in p}`) in
        tune_contacts, which forwards only what its dump holds. A tuple has no
        value to return, so it comes back as None.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Dict):
                keys = set()
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                        if k.value == "confidence":
                            value = v
            elif isinstance(node, ast.Tuple):
                keys = {e.value for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            else:
                continue
            if {"time", "x", "y"} <= keys:
                out.append((keys, value))
        return out

    @staticmethod
    def _reads_the_field(node):
        """`p.confidence` or `p["confidence"]`: the source's own value."""
        if isinstance(node, ast.Attribute):
            return node.attr == "confidence"
        if isinstance(node, ast.Subscript):
            return (isinstance(node.slice, ast.Constant)
                    and node.slice.value == "confidence")
        return False

    @pytest.mark.parametrize("rel", PRODUCERS)
    def test_the_position_dict_forwards_confidence(self, rel):
        path = self.ROOT / rel
        assert path.exists(), f"{rel} moved — this guard now protects nothing"
        key_sets = self._key_sets(path)
        assert key_sets, (
            f"{rel} no longer builds a dict listing time/x/y. Either it stopped "
            "producing position dicts — in which case drop it from PRODUCERS — "
            "or it now builds them dynamically, which this guard cannot see."
        )
        for keys, value in key_sets:
            assert "confidence" in keys, (
                f"{rel} builds a position dict without 'confidence'. Two of the "
                "thirteen columns would go constant at 0.0, which is also their "
                "empty-window fill, so nothing would fail loudly. See CF-420."
            )
            if value is not None:
                assert self._reads_the_field(value), (
                    f"{rel} writes 'confidence' as `{ast.unparse(value)}` instead "
                    "of reading the source's field. A constant or an invented "
                    "default carries the key and still yields a constant column, "
                    "on data nobody measured. See CF-420."
                )


class TestLoadersForwardTheSourceValue:
    """The two eval loaders, run on a synthetic source.

    The source scan above cannot pin these values: tune_contacts spells its
    dict as a key-tuple comprehension, and both loaders read a file whose
    contents decide what the dict holds. The values differ per sample, so a
    constant in either loader cannot match them by accident.
    """

    CONF = [0.11, 0.37, 0.52, 0.68, 0.93, 0.24]

    def _samples(self, *, with_confidence):
        out = []
        for i, c in enumerate(self.CONF):
            p = {"frame": i * 6, "time": i * 0.2, "x": 100.0 + 40.0 * i, "y": 180.0}
            if with_confidence:
                p["confidence"] = c
            out.append(p)
        return out

    def _dump(self, monkeypatch, tmp_path, *, with_confidence):
        positions = [{k: v for k, v in p.items() if k != "frame"}
                     for p in self._samples(with_confidence=with_confidence)]
        (tmp_path / "synthetic_ball_track.json").write_text(json.dumps(
            {"fps": 30.0, "frame_height": H, "positions": positions}))
        monkeypatch.setattr(tc, "RESULTS_DIR", tmp_path)
        monkeypatch.setattr(tc, "load_deadtime_fixture", lambda _id: None)

    def test_deadtime_variants_forwards_the_cache_value(self, monkeypatch, tmp_path):
        (tmp_path / "abc.json").write_text(
            json.dumps({"positions": self._samples(with_confidence=True)}))
        monkeypatch.setattr(dv, "BALL_CACHE_DIR", tmp_path)
        monkeypatch.setattr(dv, "load_deadtime_fixture", lambda _id: SimpleNamespace(
            raw={"source_video_md5": "abc", "source_frame_height": H},
            duration=2.0, keep=[]))
        game = dv.load_game("synthetic")
        assert [p["confidence"] for p in game.positions] == self.CONF

    def test_tune_contacts_forwards_the_dump_value(self, monkeypatch, tmp_path):
        self._dump(monkeypatch, tmp_path, with_confidence=True)
        _, positions, _, _ = tc.load("synthetic")
        assert [p["confidence"] for p in positions] == self.CONF

    def test_tune_contacts_omits_what_a_pre_cf420_dump_lacks(self, monkeypatch, tmp_path):
        """No `confidence` in the dump means none in the dict — not the 1.0
        its BallPosition falls back to, which would read as measured."""
        self._dump(monkeypatch, tmp_path, with_confidence=False)
        _, positions, _, _ = tc.load("synthetic")
        assert positions and not any("confidence" in p for p in positions)


class TestConfidenceColumnsReadTheField:
    def test_the_columns_track_the_supplied_values(self):
        """Non-zero coverage for both columns.

        Round 2 on #543 found `p.get("confidence", 0.0)` mutating to `1.0` with
        the suite still green: every fixture either supplied confidence or did
        not look at these columns, so no test distinguished the default from a
        measured value.
        """
        pos = track([0.5, 1.0, 1.5], conf=0.2)
        pos[1]["confidence"] = 0.8
        f = compute_features(pos, [], 3.0, H)
        assert f[1, col("mean_conf_3s")] == pytest.approx((0.2 + 0.8 + 0.2) / 3)
        assert f[1, col("max_conf_3s")] == pytest.approx(0.8)

    def test_a_sample_without_the_field_falls_to_the_documented_fill(self):
        """The half the mutation lived in: the default only shows when the key
        is absent, so it needs a fixture where it is. 0.0, not 1.0 — a missing
        measurement must not read as maximum confidence."""
        f = compute_features([{"time": 1.0, "x": 0.0, "y": 180.0}], [], 3.0, H)
        assert f[1, col("mean_conf_3s")] == 0.0
        assert f[1, col("max_conf_3s")] == 0.0
