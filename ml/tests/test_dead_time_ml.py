"""Unit tests for learned keep-window derivation (ml/pipeline/dead_time_ml.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.dead_time_ml import (
    FEATURE_NAMES,
    WEIGHTS_PATH,
    active_windows_from_ml,
    compute_features,
    load_weights,
    predict_in_play,
)


def rally_positions(t_start: float, t_end: float, speed_pxps: float = 300.0,
                    step: float = 0.33) -> list[dict]:
    """Dense fast-moving track samples, like a ball in play."""
    out, t = [], t_start
    while t <= t_end:
        out.append({"time": t, "x": (t - t_start) * speed_pxps,
                    "y": 100.0 + 50.0 * ((t * 7) % 2), "confidence": 0.8})
        t += step
    return out


class TestComputeFeatures:
    def test_shape_one_row_per_second(self):
        feats = compute_features(rally_positions(0, 10), [{"time": 5.0}], 60.0)
        assert feats.shape == (60, len(FEATURE_NAMES))

    def test_empty_inputs_zero_activity_saturated_gaps(self):
        feats = compute_features([], [], 30.0)
        assert feats.shape == (30, len(FEATURE_NAMES))
        since = FEATURE_NAMES.index("since_contact")
        until = FEATURE_NAMES.index("until_contact")
        assert (feats[:, since] == 60.0).all()
        assert (feats[:, until] == 60.0).all()
        rate = FEATURE_NAMES.index("track_rate_2s")
        assert (feats[:, rate] == 0.0).all()

    def test_active_second_has_more_signal_than_dead_second(self):
        positions = rally_positions(0, 10)
        feats = compute_features(positions, [{"time": 5.0}], 120.0)
        rate = FEATURE_NAMES.index("track_rate_2s")
        assert feats[5, rate] > feats[100, rate]

    def test_tracking_dropout_contributes_no_speed(self):
        # Two samples 10s apart: spacing > MAX_SAMPLE_SPACING, so no speed sample
        positions = [
            {"time": 0.0, "x": 0.0, "y": 0.0, "confidence": 0.5},
            {"time": 10.0, "x": 5000.0, "y": 0.0, "confidence": 0.5},
        ]
        feats = compute_features(positions, [], 20.0)
        speed = FEATURE_NAMES.index("mean_speed_3s")
        assert (feats[:, speed] == 0.0).all()


class TestWeights:
    def test_committed_weights_load_and_match_contract(self):
        w = load_weights()
        assert w["features"] == FEATURE_NAMES
        assert len(w["coef"]) == len(FEATURE_NAMES)
        assert len(w["mean"]) == len(FEATURE_NAMES)
        assert len(w["std"]) == len(FEATURE_NAMES)
        assert 0.0 < w["threshold"] < 1.0

    def test_feature_mismatch_raises(self, tmp_path):
        bad = tmp_path / "weights.json"
        bad.write_text('{"features": ["not", "the", "right", "ones"]}')
        with pytest.raises(ValueError):
            load_weights(bad)


class TestPredictInPlay:
    def test_probabilities_bounded(self):
        feats = compute_features(rally_positions(0, 30), [{"time": 10.0}], 60.0)
        probs = predict_in_play(feats, load_weights())
        assert probs.shape == (60,)
        assert ((probs >= 0.0) & (probs <= 1.0)).all()


class TestActiveWindowsFromMl:
    def test_empty_inputs_no_windows(self):
        assert active_windows_from_ml([], [], 600.0) == []
        assert active_windows_from_ml(rally_positions(0, 5), [], 0.0) == []

    def test_windows_sorted_disjoint_and_clamped(self):
        positions = rally_positions(30, 60) + rally_positions(200, 240)
        contacts = [{"time": t} for t in (35.0, 45.0, 55.0, 210.0, 225.0)]
        windows = active_windows_from_ml(positions, contacts, 300.0)
        for start, end in windows:
            assert 0.0 <= start < end <= 300.0
        for (_, e1), (s2, _) in zip(windows, windows[1:]):
            assert e1 < s2

    def test_rally_kept_long_dead_stretch_cut(self):
        # One clear rally, then nothing for eight minutes
        positions = rally_positions(20, 50)
        contacts = [{"time": t} for t in (25.0, 33.0, 41.0, 48.0)]
        windows = active_windows_from_ml(positions, contacts, 500.0)
        assert windows, "the rally should produce a keep-window"
        covered = sum(e - s for s, e in windows)
        assert covered < 250.0, "most of the dead file should be cut"
        # the rally midpoint must be inside some window
        assert any(s <= 35.0 <= e for s, e in windows)

    def test_missing_weights_file_raises_for_caller_fallback(self, tmp_path):
        with pytest.raises(Exception):
            active_windows_from_ml(
                rally_positions(0, 10), [{"time": 5.0}], 60.0,
                weights=load_weights(tmp_path / "absent.json"),
            )

    def test_committed_weights_file_exists(self):
        assert WEIGHTS_PATH.exists(), (
            "deadtime_ml_weights.json must ship next to dead_time_ml.py — "
            "condense_use_ml falls back (loudly) without it"
        )


class TestSmoothing:
    def test_probability_smoothing_is_padded_correctly(self):
        # A single-second activity blip should not survive 5s smoothing at the
        # shipped threshold — guards against np.convolve mode changes.
        feats = compute_features(rally_positions(10, 11), [], 30.0)
        probs = predict_in_play(feats, load_weights())
        w = load_weights()
        assert (probs >= w["threshold"]).sum() <= 5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
