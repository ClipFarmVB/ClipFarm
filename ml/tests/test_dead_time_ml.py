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
        """One of five copies of 0.30, pinned to the one that defines it.

        The five, so the two files carrying this count say the same list:
        `motion_anchor_windows(speed=...)`'s default, which defines it and is
        what this pins; `FAST_SPEED_FH` in ml/pipeline/dead_time_ml.py, the
        copy this test exists for; `active_windows_guarded(anchor_speed=...)`,
        which passes it down; `condense_guard_anchor_speed` in
        api/app/config.py, which is what production actually sends; and a
        hardcoded `speed=0.30` in ml/eval/deadtime_variants.py:169. This pin
        reaches exactly one of them.

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
        """Every producer in this repo builds {"time","x","y"} and drops the
        `confidence` the tracker supplies, so both conf columns are constant
        0.0 on real input — and 0.0 is also their empty-window fill, making
        "not supplied" indistinguishable from "no samples". A silent constant
        column is what the trainer would learn from."""
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
