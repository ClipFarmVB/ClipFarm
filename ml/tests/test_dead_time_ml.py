"""
Feature extraction for the learned dead-time builder (CF-392).

No conftest.py exists anywhere in this repo, so every file in ml/tests puts the
project root on sys.path itself. Matching test_dead_time.py.
"""
import inspect
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline import dead_time as dt  # noqa: E402
from ml.pipeline.dead_time_ml import (  # noqa: E402
    CONTACT_GAP_CAP,
    EMPTY_MEAN_Y,
    FAST_SPEED_FH,
    FEATURE_NAMES,
    FEATURE_VERSION,
    compute_features,
    load_weights,
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
    def test_thirteen_columns_at_version_three(self):
        assert len(FEATURE_NAMES) == 13
        assert FEATURE_VERSION == 3
        assert len(set(FEATURE_NAMES)) == 13, "a duplicated column name"

    def test_the_fast_bar_is_the_anchors_bar(self):
        """The third copy of 0.30, pinned to the one it was taken from.

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

    def test_max_columns_exceed_their_means_when_the_window_varies(self):
        """max_* are real reductions, not copies of the mean."""
        pos = track([0.5, 1.0, 1.5], conf=0.2)
        pos[1]["confidence"] = 0.95
        f = compute_features(pos, [], 3.0, H)
        assert f[1, col("max_conf_3s")] > f[1, col("mean_conf_3s")]
        assert f[1, col("max_conf_3s")] == pytest.approx(0.95)


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

    def test_unvalidated_weights_load_but_warn(self, tmp_path, caplog):
        p = self._write(tmp_path, validated=False)
        with caplog.at_level("WARNING"):
            load_weights(p)
        assert "NOT validated" in caplog.text

    def test_there_is_no_committed_weights_file_yet(self):
        """Pins the docstring's claim. When CF-393 commits one, this fails and
        the docstring gets rewritten with it."""
        from ml.pipeline.dead_time_ml import WEIGHTS_PATH

        assert not WEIGHTS_PATH.exists()


class TestGuards:
    def test_a_non_positive_frame_height_is_refused(self):
        with pytest.raises(ValueError, match="frame_height"):
            compute_features(track([1.0]), [], 5.0, 0)

    def test_pixel_columns_are_frame_relative(self):
        """The same motion at two resolutions gives the same features."""
        small = compute_features(track([0.5, 1.0, 1.5], y=90.0, step=36.0), [], 3.0, 360)
        large = compute_features(track([0.5, 1.0, 1.5], y=270.0, step=108.0), [], 3.0, 1080)
        np.testing.assert_allclose(small, large)
