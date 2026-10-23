"""
Per-second in-play features and the learned dead-time builder (CF-392, CF-394).

One row per whole second of the game, describing the ball track around that
second: how densely it is tracked, how fast the ball is moving, how close the
nearest contacts are, and where in the frame the ball sits. The learned builder
scores those rows.

**Scope.** Features, the weights contract, the numpy helpers that turn a
feature matrix into keep-windows — `robust_scale`, `add_context`,
`predict_in_play` and `windows_from_probability` (CF-393), the HMM decoder
and the `decode_windows` dispatch between the two (CF-396) — and the builder
that chains them, `active_windows_learned` (CF-394). The trainer
(`ml/eval/train_deadtime.py`) and the builder both call the same helpers, so
the two cannot prepare a matrix differently.

The weights file (`deadtime_ml_weights.json`, CF-393) is **unvalidated** —
`"validated": false` until CF-395 applies the epic's bar — and the builder
refuses unvalidated weights unless its caller passes `allow_unvalidated=True`.
Production reaches it only through `condense_mode="learned"`, which is not the
default, and never passes that flag; the condense stage falls back to
`guarded` instead. The eval ladder's `v6` passes it, so the weights can be
scored before they are validated.

Ported from the CF-173 branch and changed in two ways to match current main:

1. **Speeds come from `dead_time.speed_samples()`**, which returns NaN for a
   displacement above `MAX_PLAUSIBLE_SPEED_FH` rather than dropping it — the
   tracker jumping between two objects still proves a sample exists. Its
   docstring requires consumers to mask rather than propagate, and the naive
   windowed mean does propagate: the helper is a cumulative sum, so once a NaN
   enters it, `cum[hi] - cum[lo]` is NaN for every window whose right edge
   reaches past it — including windows centred *before* it. `_window_stats`
   masks.

2. **The fast bar is the anchor's 0.30 fh/s, not CF-173's 150/360 = 0.4167.**
   `fast_fraction_5s` therefore reads materially higher on the same track than
   it did on that branch. That is intended, and it is why FEATURE_VERSION moves.

Unchanged from CF-173, and listed because it is easy to assume otherwise:
pixel quantities are divided by frame height, so a 360p cache and a 1080p upload
share one feature space. That was already true on the branch — its own
`FEATURE_VERSION = 2` note records it as the v2 change.

**The two NaN conventions on main, and which one each column uses.** They differ
and the choice matters:

* `motion_anchor_windows` (dead_time.py) counts an over-ceiling sample in the
  denominator of its fast-fraction, so the sample votes *not fast*.
* `speed_gate_contacts` masks them out, and calls an all-NaN window "the same
  situation as a window with no samples at all: unjudged".

`fast_fraction_5s` is structurally the anchor's fast-fraction, so it keeps the
anchor's convention — an unbelievable sample votes not-fast. The speed
*magnitude* columns mask instead, because an unjudgeable magnitude cannot be
averaged. An all-NaN window then reaches the same fill as an empty one, which is
the house answer; note it means *dead* here, where `speed_gate_contacts` reads
"unjudged" as benefit of the doubt.

The module imports only numpy, `ml.pipeline.dead_time` and
`ml.pipeline.intervals` — `ml/tests` runs with numpy alone in CI.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np

from ml.pipeline.dead_time import speed_samples, track_is_usable
from ml.pipeline.intervals import Interval, merge_intervals

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).with_name("deadtime_ml_weights.json")

# Order matters: must match the "features" list in a weights file.
#
# Every column's value for a second with no usable samples in its window is
# given beside it as `fill=`. The matrix never contains NaN, and an all-NaN
# window is treated as an empty one.
FEATURE_NAMES = [
    "track_rate_2s",       # ball-track samples per second within ±2s.  fill=0
    "track_coverage_10s",  # fraction of ±10s one-second bins with any sample. fill=0
    "mean_speed_3s",       # mean speed (frame-heights/s) within ±3s, NaN masked. fill=0
    "fast_fraction_5s",    # share of ±5s speed samples ≥ FAST_SPEED_FH. fill=0
    "contacts_5s",         # contacts within ±5s.  fill=0
    "contacts_15s",        # contacts within ±15s. fill=0
    "since_contact",       # seconds since previous contact, capped. fill=CONTACT_GAP_CAP
    "until_contact",       # seconds until next contact, capped.     fill=CONTACT_GAP_CAP
    "mean_conf_3s",        # mean tracker confidence within ±3s. fill=0
    "y_std_5s",            # std of ball y (frame-heights) within ±5s. fill=0
    # --- added in v3 ---
    #
    # fill=0.5, NOT 0. Image y grows DOWNWARD, so a small value means the ball
    # is high in the frame — airborne, in play. Zero would therefore tell the
    # model "maximally airborne" for a second with no evidence at all: the
    # strongest in-play vote for the emptiest data, with the sign inverted
    # against every other fill in this matrix. 0.5 is the frame centre, which
    # commits to neither. This is the one column where the obvious fill is
    # actively wrong.
    "mean_y_5s",           # mean ball y (frame-heights) within ±5s. fill=0.5
    "max_conf_3s",         # max tracker confidence within ±3s. fill=0
    "max_speed_3s",        # max speed (fh/s) within ±3s, NaN masked. fill=0
]

# v2 -> v3: three columns added (mean_y_5s, max_conf_3s, max_speed_3s), speeds
# now sourced from dead_time.speed_samples with NaN masking, and the fast bar
# moved from 150/360 to the anchor's 0.30. load_weights rejects another version.
#
# v3 -> v4 (CF-420): no column added or removed — two of them changed MEANING.
# Under v3 every producer dropped `confidence`, so mean_conf_3s and max_conf_3s
# were constant 0.0 on real input; they now carry the tracker's own value. A
# model fitted on v3 learned those two columns as constants, so its coefficients
# for them say nothing about v4 input — which is what the version exists to
# catch. Nothing had trained on v3 when this landed, so the bump cost nothing.
FEATURE_VERSION = 4

# The per-sample "this is fast" bar, in frame-heights/s.
#
# A FIFTH copy of 0.30, and the count matters because each one is a place it
# can drift. The others: `motion_anchor_windows(speed=...)`'s default, which
# actually applies it; `active_windows_guarded(anchor_speed=...)`, which passes
# it down; `condense_guard_anchor_speed` in api/app/config.py, which is what
# PRODUCTION passes (tasks.py), and which is unreachable from here since
# ml/pipeline must not import app.config; and a hardcoded `speed=0.30` in
# ml/eval/deadtime_variants.py:169.
#
# This said FOURTH and omitted that last one, while
# `test_dead_time_ml.py`'s own docstring said five and listed it — the two
# files disagreeing inside the sentence that argues the count matters.
#
# So the test below pins this to `motion_anchor_windows`' default — the one that
# defines the quantity — and NOT to the value production actually runs with. If
# an operator moves the config setting, this stays put and nothing notices. That
# is a real limit of the pin, not a claim about it.
#
# NOT `gate_speed` (0.25): that is a median over ±1.5s used to judge a contact's
# credibility, a different quantity that dead_time.py's own comments warn
# against conflating with this one.
FAST_SPEED_FH = 0.30

CONTACT_GAP_CAP = 60.0   # since/until saturate here — beyond this it is just "long ago"
EMPTY_MEAN_Y = 0.5       # frame centre; see mean_y_5s above

# Moving-average width, in seconds, for the probability trace (from CF-173).
SMOOTH_SECONDS = 5

# The lagged-context offsets the trainer's context variant uses, in seconds.
# Negative reads the past: lag -5 puts second t-5's features on row t.
CONTEXT_LAGS = (-5, -3, -1, 1, 3, 5)


def compute_features(
    positions: list[dict],
    contacts: list[dict],
    duration: float,
    frame_height: int,
) -> np.ndarray:
    """
    Per-second feature matrix, one row per whole second of [0, duration).

    `positions` are ball-track samples ({"time", "x", "y", "confidence"?}) in the
    source video's pixel space; `frame_height` is that video's height, used to
    normalize the pixel-derived columns. `contacts` are find_contacts() output
    ({"time", ...}). Both may be empty.

    The returned matrix is always finite — every column has a documented fill for
    a second whose window holds nothing usable, listed beside its name.
    """
    if frame_height <= 0:
        raise ValueError(f"frame_height must be positive, got {frame_height}")

    # `confidence` is optional in this dict shape, and until CF-420 every
    # producer in the repository dropped it — so mean_conf_3s and max_conf_3s
    # were constant 0.0 on real input. All five forward it now:
    #
    #   api/app/workers/tasks.py         ml/eval/harness.py
    #   ml/eval/diagnose_detection.py    ml/eval/deadtime_variants.py
    #   ml/eval/tune_contacts.py         (as far as the dump it reads carries it)
    #
    # Two test classes in ml/tests/test_dead_time_ml.py pin this, and neither
    # covers every case alone. TestProducersForwardConfidence parses all five
    # and fails if one drops the key; for the four that build a dict literal
    # (tasks, harness, diagnose_detection, deadtime_variants) it also fails if
    # the value is a constant rather than the source's field. tune_contacts
    # builds its dict from a key tuple, which has no value to scan, so a
    # constant there passes that class: only TestLoadersForwardTheSourceValue,
    # which runs tune_contacts.load and deadtime_variants.load_game on
    # synthetic input, catches it. Both are needed because the loss is silent
    # here: 0.0 is also these columns' documented empty-window fill, so "not
    # supplied" and "no samples in this window" are indistinguishable once
    # inside the matrix.
    #
    # The warning below therefore no longer describes the normal state. Outside
    # ml/tests there are three callers: CF-393's trainer
    # (ml/eval/train_deadtime.py, via deadtime_variants.load_game), the eval
    # ladder's v6 rung (deadtime_variants), and active_windows_learned, which
    # the worker reaches when condense_mode="learned" (CF-394). A warning in
    # worker logs therefore means the live track the worker scored lost the
    # field, not only a training input: either way it is a regressed producer,
    # or a track dumped before CF-420.
    if positions and not any("confidence" in p for p in positions):
        logger.warning(
            "no ball-track sample carries 'confidence' — mean_conf_3s and "
            "max_conf_3s will be constant 0.0, indistinguishable from an "
            "unsampled second. Every producer forwards it since CF-420, so "
            "this is a regressed producer or a track dump predating it."
        )
    n = max(1, int(math.ceil(duration)))
    centers = np.arange(n, dtype=np.float64) + 0.5

    pts = sorted((p["time"], p["x"], p["y"], p.get("confidence", 0.0)) for p in positions)
    p_times = np.array([p[0] for p in pts], dtype=np.float64)
    p_y = np.array([p[2] for p in pts], dtype=np.float64) / frame_height
    p_conf = np.array([p[3] for p in pts], dtype=np.float64)

    # Main's speeds, not a local loop: over-ceiling displacements arrive as NaN.
    sp_times, sp_values = speed_samples(positions, frame_height)

    # Which one-second bins contain any track sample (for coverage).
    bin_has_sample = np.zeros(n, dtype=bool)
    if len(p_times):
        idx = np.clip(p_times.astype(int), 0, n - 1)
        bin_has_sample[idx] = True
    coverage_cum = np.concatenate(([0], np.cumsum(bin_has_sample)))

    def _bounds(sorted_times: np.ndarray, half: float):
        return (
            np.searchsorted(sorted_times, centers - half),
            np.searchsorted(sorted_times, centers + half),
        )

    def _window_stats(sorted_times: np.ndarray, values: np.ndarray, half: float,
                      default: float) -> tuple[np.ndarray, np.ndarray]:
        """(mean, max) over each window, ignoring NaN, `default` where none remain.

        Both reductions are taken from the same masked sums so they cannot
        disagree about which samples counted. The mean uses a cumulative sum of
        the NaN-zeroed values against a cumulative count of the finite ones; the
        max cannot be done cumulatively at all and is taken per window.
        """
        lo, hi = _bounds(sorted_times, half)
        finite = np.isfinite(values)
        zeroed = np.where(finite, values, 0.0)
        cum = np.concatenate(([0.0], np.cumsum(zeroed)))
        cnt = np.concatenate(([0], np.cumsum(finite)))
        count = (cnt[hi] - cnt[lo]).astype(np.float64)

        mean = np.full(n, default, dtype=np.float64)
        nz = count > 0
        mean[nz] = (cum[hi] - cum[lo])[nz] / count[nz]

        peak = np.full(n, default, dtype=np.float64)
        for i in np.nonzero(nz)[0]:
            chunk = values[lo[i]:hi[i]]
            peak[i] = np.max(chunk[np.isfinite(chunk)])
        return mean, peak

    feats = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float64)

    lo, hi = _bounds(p_times, 2.0)
    feats[:, 0] = (hi - lo) / 4.0

    b_lo = np.clip((centers - 10.0).astype(int), 0, n)
    b_hi = np.clip((centers + 10.0).astype(int) + 1, 0, n)
    feats[:, 1] = (coverage_cum[b_hi] - coverage_cum[b_lo]) / np.maximum(b_hi - b_lo, 1)

    mean_speed_3s, max_speed_3s = _window_stats(sp_times, sp_values, 3.0, 0.0)
    feats[:, 2] = mean_speed_3s
    feats[:, 12] = max_speed_3s

    # The anchor's convention, deliberately NOT the masked one: `NaN >= x` is
    # False, and the denominator counts every sample in the window, so an
    # unbelievable displacement votes "not fast" exactly as it does in
    # motion_anchor_windows. See the module docstring.
    lo, hi = _bounds(sp_times, 5.0)
    fast_cum = np.concatenate(([0.0], np.cumsum((sp_values >= FAST_SPEED_FH).astype(np.float64))))
    span = (hi - lo).astype(np.float64)
    nz = span > 0
    feats[nz, 3] = (fast_cum[hi] - fast_cum[lo])[nz] / span[nz]

    c_times = np.array(sorted(c["time"] for c in contacts), dtype=np.float64)
    lo, hi = _bounds(c_times, 5.0)
    feats[:, 4] = hi - lo
    lo, hi = _bounds(c_times, 15.0)
    feats[:, 5] = hi - lo

    feats[:, 6] = CONTACT_GAP_CAP
    feats[:, 7] = CONTACT_GAP_CAP
    if len(c_times):
        prev_idx = np.searchsorted(c_times, centers, side="right") - 1
        has_prev = prev_idx >= 0
        feats[has_prev, 6] = np.minimum(
            centers[has_prev] - c_times[prev_idx[has_prev]], CONTACT_GAP_CAP
        )
        next_idx = np.searchsorted(c_times, centers, side="left")
        has_next = next_idx < len(c_times)
        feats[has_next, 7] = np.minimum(
            c_times[next_idx[has_next]] - centers[has_next], CONTACT_GAP_CAP
        )

    mean_conf_3s, max_conf_3s = _window_stats(p_times, p_conf, 3.0, 0.0)
    feats[:, 8] = mean_conf_3s
    feats[:, 11] = max_conf_3s

    # y-std within ±5s: E[y²] − E[y]² on the same windowed sums. `mean_y` is the
    # new column as well as an input here — same window, so exposing it is free.
    mean_y, _ = _window_stats(p_times, p_y, 5.0, EMPTY_MEAN_Y)
    mean_y2, _ = _window_stats(p_times, p_y * p_y, 5.0, EMPTY_MEAN_Y * EMPTY_MEAN_Y)
    feats[:, 9] = np.sqrt(np.maximum(mean_y2 - mean_y * mean_y, 0.0))
    feats[:, 10] = mean_y

    return feats


class LearnedBuilderDeclined(ValueError):
    """`active_windows_learned` will not judge this game, by design.

    Distinct from a failure so the condense stage can log it as the expected
    fallback it is (INFO) rather than as a broken builder (WARNING). Both fall
    back to `guarded` the same way; only the log level differs. A ValueError so
    a caller that only knows "the builder raised" still treats it as one.
    """


class UnvalidatedWeightsError(LearnedBuilderDeclined):
    """The weights match the contract but carry `"validated": false`, and the
    caller did not pass `allow_unvalidated=True`."""


class TrackTooSparseError(LearnedBuilderDeclined):
    """The ball track is empty, or below `min_track_rate` usable speed samples
    per second — the same test `active_windows_guarded` abstains on."""


def check_contract(weights: dict, where: str = "deadtime weights") -> None:
    """
    Refuse a weights dict this code cannot apply: a different feature list or
    feature version, a different preparation record (`check_preparation`), or
    a decoder this code does not know or an hmm record it cannot use
    (`check_decoder`, CF-396; a missing `"decoder"` is "threshold").

    Split out of `load_weights` so a dict that did not come through it — the
    builder's `weights=` argument — is held to the same contract.
    """
    if weights.get("features") != FEATURE_NAMES:
        raise ValueError(
            f"{where} were trained for features "
            f"{weights.get('features')}, code expects {FEATURE_NAMES}"
        )
    if weights.get("feature_version") != FEATURE_VERSION:
        raise ValueError(
            f"{where} are feature_version "
            f"{weights.get('feature_version')}, code expects {FEATURE_VERSION} — retrain"
        )
    check_preparation(weights, where)
    check_decoder(weights, where)


def load_weights(path: Path = WEIGHTS_PATH, *, warn_unvalidated: bool = True) -> dict:
    """
    Load a weights file, validating the feature contract.

    The committed file (`deadtime_ml_weights.json`) is written by
    `python -m ml.eval.train_deadtime` (CF-393) and is **unvalidated**: the
    trainer always writes `"validated": false`, and flipping it is CF-395's
    call after applying the epic's bar. Committing weights is not enabling
    them: `active_windows_learned` refuses unvalidated weights unless told
    otherwise, and `condense_mode="learned"` is not the default.

    A weights file carries "validated": true only once its leave-one-game-out
    numbers justify enabling it. Unvalidated weights still load, so the flag
    stays usable for experiments, and warn unless `warn_unvalidated=False`.
    The condense stage passes False: the builder refuses those weights and the
    stage logs that refusal itself, so the warning would be a second line about
    the same fact.
    """
    data = json.loads(path.read_text())
    check_contract(data, f"deadtime weights at {path}")
    if warn_unvalidated and data.get("validated") is not True:
        logger.warning(
            "deadtime weights at %s are NOT validated (trained on %s) — "
            "keep-windows from them are unproven on unseen footage",
            path, data.get("trained_on", "?"),
        )
    return data


# ── matrix preparation and inference (CF-393) ──────────────────────────────
#
# Shared by the trainer and the builder (CF-394), so the two cannot
# prepare a matrix differently. The weights file records the rule each one
# applies ("scaling", "context"); these functions are that rule.
#
# SCALING and the context's edge/layout are that record, kept beside the code
# they describe. `load_weights` and `predict_in_play` refuse a weights dict
# whose record differs, so a change to `robust_scale` or `add_context` that
# updates its record here also refuses the committed weights until they are
# retrained; the column count alone cannot catch it. FEATURE_VERSION plays the
# same part for the features. Change the record whenever the rule changes.
SCALING = {
    "rule": "per_video_robust",
    "center": "median",
    "range": "p90-p10",
    "zero_range": "max-min, then divide_by_1",
}
CONTEXT_EDGE = "replicate"
CONTEXT_LAYOUT = "base columns, then one block of all of them per lag, in lags order"


def context_rule(lags: tuple[int, ...] | list[int]) -> dict:
    """The `"context"` record for a model prepared with `lags`."""
    return {"lags": list(lags), "edge": CONTEXT_EDGE, "layout": CONTEXT_LAYOUT}


def check_preparation(weights: dict, where: str = "deadtime weights") -> None:
    """
    Refuse weights whose recorded `"scaling"` or `"context"` rule is not the one
    `robust_scale` and `add_context` apply. A missing record is refused too: a
    coef vector with no record of its preparation cannot be trusted to match.
    """
    if weights.get("scaling") != SCALING:
        raise ValueError(
            f"{where} were scaled by {weights.get('scaling')}, code scales by "
            f"{SCALING} — retrain"
        )
    context = weights.get("context") or {}
    rule = {k: context.get(k) for k in ("edge", "layout")}
    expected = {"edge": CONTEXT_EDGE, "layout": CONTEXT_LAYOUT}
    if rule != expected:
        raise ValueError(
            f"{where} were given context {rule}, code builds {expected} — retrain"
        )

def robust_scale(features: np.ndarray) -> np.ndarray:
    """
    Per-video robust scaling: each column minus its median, divided by its
    p10-p90 range, both taken over this one video's rows.

    Per video, not a global mean/std, because condense is a batch job — the
    whole track exists at inference, so each game can be put on its own scale.
    That absorbs per-venue offsets (a gym where the ball sits lower in frame, a
    tracker that is more confident on one ball) that a global fit would have to
    learn as signal. It is what the epic's experiment measured.

    A column can have a zero p10-p90 range without being constant: when more
    than 80% of its rows share one value. On the fixtures that is a saturated
    column (test4's `track_rate_2s` sits at 3.0 bar 2.5% of rows) or a sparse
    one (test3's `contacts_5s` is non-zero on 9.9% of rows). Such a column
    falls back to its full range, max - min, so it is still range-scaled like
    the same column in every other game rather than left in raw units. Only a
    column that is truly constant (or whose ranges are not finite) is divided
    by 1, so it centres to zeros rather than to NaN or inf.
    """
    X = np.asarray(features, dtype=np.float64)
    if X.shape[0] == 0:
        return X.copy()
    center = np.median(X, axis=0)
    spread = np.percentile(X, 90, axis=0) - np.percentile(X, 10, axis=0)
    full = X.max(axis=0) - X.min(axis=0)
    spread = np.where(np.isfinite(spread) & (spread > 0), spread,
                      np.where(np.isfinite(full) & (full > 0), full, 1.0))
    out: np.ndarray = (X - center) / spread
    return out


def add_context(
    scaled: np.ndarray, lags: tuple[int, ...] | list[int] = CONTEXT_LAGS,
) -> np.ndarray:
    """
    Append a shifted copy of every column per lag: the result is the input's
    columns, then one block of all of them for each lag, in `lags` order.

    Lag `k` puts row `t + k`'s values on row `t` — negative reads the past.
    Rows that would read off either end take the edge row instead (replicate),
    so the first second's "5s ago" is the first second itself. Applied after
    `robust_scale`, so the lagged blocks share the base columns' scale.

    An empty `lags` returns the input unchanged (as a copy).
    """
    X = np.asarray(scaled, dtype=np.float64)
    n = X.shape[0]
    if n == 0:
        return np.zeros((0, X.shape[1] * (1 + len(lags))))
    rows = np.arange(n)
    blocks = [X] + [X[np.clip(rows + int(lag), 0, n - 1)] for lag in lags]
    out: np.ndarray = np.hstack(blocks)
    return out


def design_matrix(features: np.ndarray, lags: tuple[int, ...] | list[int]) -> np.ndarray:
    """`compute_features` output → the matrix a weights file's `coef` multiplies."""
    return add_context(robust_scale(features), lags)


def smooth_probability(probs: np.ndarray) -> np.ndarray:
    """
    Centred moving average over SMOOTH_SECONDS (CF-173's smoothing), one value
    per input row.

    Not `np.convolve(mode="same")`, which returns max(len(probs),
    len(kernel)) values, so a video shorter than the kernel came back longer
    than it went in. Slicing the full convolution at the kernel's centre gives
    the same values as "same" for any trace at least as long as the kernel.
    """
    kernel = np.ones(SMOOTH_SECONDS) / SMOOTH_SECONDS
    start = (SMOOTH_SECONDS - 1) // 2
    full = np.convolve(np.asarray(probs, dtype=np.float64), kernel, mode="full")
    out: np.ndarray = full[start: start + len(probs)]
    return out


def predict_in_play(features: np.ndarray, weights: dict, *, smooth: bool = True) -> np.ndarray:
    """
    Per-second P(in-play) from a raw `compute_features` matrix.

    Applies the weights file's own preparation — per-video robust scaling, then
    the lags in `weights["context"]["lags"]` — so a caller cannot pair a coef
    vector with the wrong layout. The scaling and edge rule are the code's, so
    weights whose recorded `"scaling"` or context edge/layout differ from them
    are refused (`check_preparation`) rather than silently mis-scaled.
    `smooth=False` returns the raw sigmoid, which
    is what held-out AUC is computed on; the windows use the smoothed trace.
    """
    check_preparation(weights)
    lags = list(weights.get("context", {}).get("lags", []))
    X = design_matrix(features, lags)
    coef = np.asarray(weights["coef"], dtype=np.float64)
    if X.shape[1] != coef.shape[0]:
        raise ValueError(
            f"weights have {coef.shape[0]} coefficients, the prepared matrix has "
            f"{X.shape[1]} columns ({X.shape[1] // (1 + len(lags))} features x "
            f"{1 + len(lags)} blocks)"
        )
    logits = X @ coef + float(weights["intercept"])
    probs: np.ndarray = 1.0 / (1.0 + np.exp(-logits))
    return smooth_probability(probs) if smooth else probs


def windows_from_probability(
    probs: np.ndarray,
    threshold: float,
    duration: float,
    *,
    pad_before: float = 3.0,
    pad_after: float = 2.5,
    merge_gap_seconds: float = 5.0,
) -> list[Interval]:
    """
    Per-second probabilities → padded, merged keep-windows (the shape of
    CF-173's `active_windows_from_ml`).

    Row `i` covers second [i, i+1). Every run of rows at or above `threshold`
    becomes a span, padded by `pad_before`/`pad_after`, clamped to
    [0, duration], and merged with any neighbour closer than
    `merge_gap_seconds`.

    The pads are tighter than the condense_pad_* settings on purpose: those pad
    sparse contact anchors, while these pad spans that already cover the
    detected play. The trainer picks the threshold against these defaults, so
    a caller changing them is no longer using the threshold it was tuned for.
    """
    if duration <= 0 or len(probs) == 0:
        return []
    keep = np.asarray(probs) >= threshold
    return _runs_to_windows(keep, duration, pad_before=pad_before, pad_after=pad_after,
                            merge_gap_seconds=merge_gap_seconds)


def _runs_to_windows(
    keep: np.ndarray,
    duration: float,
    *,
    pad_before: float,
    pad_after: float,
    merge_gap_seconds: float,
) -> list[Interval]:
    """
    Per-second in-play flags → padded, merged keep-windows: the tail both
    decoders share (CF-396), so the pads and merge gap mean the same thing
    whichever one produced the flags. Split out of `windows_from_probability`
    unchanged.
    """
    if duration <= 0 or len(keep) == 0:
        return []
    windows: list[Interval] = []
    start: int | None = None
    for i, k in enumerate(keep):
        if k and start is None:
            start = i
        elif not k and start is not None:
            windows.append((float(start), float(i)))
            start = None
    if start is not None:
        windows.append((float(start), float(len(keep))))

    padded = [
        (max(0.0, s - pad_before), min(float(duration), e + pad_after))
        for s, e in windows
    ]
    return merge_intervals([w for w in padded if w[1] > w[0]], merge_gap_seconds)


# ── decoding: smooth-and-threshold or a two-state HMM (CF-396) ─────────────
#
# A weights file's `"decoder"` names how its per-second probabilities become
# keep-windows. "threshold" is the CF-393 path — `smooth_probability`, then
# `windows_from_probability` at `"threshold"` — and a file without the key is
# read as it, since every file written before CF-396 was decoded that way.
# "hmm" is `viterbi_windows` over the RAW probabilities, with the transition
# record in `"hmm"`. `decode_windows` is the one dispatch; the builder and the
# trainer both go through it, so hmm weights cannot be decoded by threshold.
DECODERS = ("threshold", "hmm")
DEFAULT_DECODER = "threshold"
HMM_KEYS = ("p_stay_play", "p_stay_dead", "prior_play")

# Guards on fitted transitions against degenerate label sets, not tuning: the
# five fixtures' folds land well inside both (the trainer prints them). Below
# 0.5 a state would rather switch than stay — a mean run under two seconds,
# which is not a duration model — and above 0.999 (a mean run over 1000s) one
# state would be all but absorbing. The prior is kept off 0 and 1 because it
# enters in log space.
STAY_BOUNDS = (0.5, 0.999)
PRIOR_BOUNDS = (0.01, 0.99)

# Emission floor: a sigmoid can reach 0.0 or 1.0 in float64, and log(0) would
# make a state impossible on one second's word.
_EMISSION_EPS = 1e-6


def decoder_of(weights: dict) -> str:
    """The weights' decoder, `DEFAULT_DECODER` when the key is absent."""
    return str(weights.get("decoder", DEFAULT_DECODER))


def check_decoder(weights: dict, where: str = "deadtime weights") -> None:
    """
    Refuse an unknown `"decoder"`, or hmm weights whose `"hmm"` record is not
    exactly HMM_KEYS, each a real number strictly between 0 and 1.

    Refused rather than defaulted: an unknown decoder read as "threshold" would
    decode probabilities by a rule the weights were not built for.
    """
    decoder = weights.get("decoder", DEFAULT_DECODER)
    if decoder not in DECODERS:
        raise ValueError(f"{where} name decoder {decoder!r}, code knows {list(DECODERS)}")
    if decoder != "hmm":
        return
    record = weights.get("hmm")
    if not isinstance(record, dict) or sorted(record) != sorted(HMM_KEYS):
        raise ValueError(
            f"{where} use decoder 'hmm' with record {record!r}, expected keys "
            f"{list(HMM_KEYS)} — retrain"
        )
    for key in HMM_KEYS:
        value = record[key]
        # A bool passes isinstance(int) but is 0.0 or 1.0, so the range refuses it.
        if not isinstance(value, (int, float)) or not (0.0 < float(value) < 1.0):
            raise ValueError(f"{where} hmm {key} is {value!r}, expected a number in (0, 1)")


def _run_lengths(y: np.ndarray) -> tuple[list[int], list[int]]:
    """(play run lengths, dead run lengths) of one game's 0/1 labels."""
    play: list[int] = []
    dead: list[int] = []
    labels = np.asarray(y) >= 0.5
    i = 0
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        (play if labels[i] else dead).append(j - i)
        i = j
    return play, dead


def fit_transitions(label_sets: list[np.ndarray] | tuple[np.ndarray, ...]) -> dict:
    """
    The `"hmm"` record from per-second labels, one array per game.

    Runs are counted per game, so no run spans two videos. Durations are
    modelled as geometric, so each stay probability is 1 − 1/(mean run length
    of that state), pooled over every run in every game passed, then clamped to
    STAY_BOUNDS. Runs cut off by the start or end of a video count at the
    length they show, which shortens the means slightly. `prior_play` is the
    in-play fraction of all seconds passed, clamped to PRIOR_BOUNDS.

    Values are rounded to 6dp here, so a fold decodes with exactly the numbers
    a weights file records. Raises when either state never occurs — there is no
    duration to fit.
    """
    play: list[int] = []
    dead: list[int] = []
    total = in_play = 0
    for y in label_sets:
        p, d = _run_lengths(y)
        play += p
        dead += d
        total += len(y)
        in_play += int(sum(p))
    if not play or not dead:
        raise ValueError("fit_transitions needs at least one play and one dead run")

    def stay(runs: list[int]) -> float:
        raw = 1.0 - 1.0 / float(np.mean(runs))
        return round(min(max(raw, STAY_BOUNDS[0]), STAY_BOUNDS[1]), 6)

    prior = min(max(in_play / total, PRIOR_BOUNDS[0]), PRIOR_BOUNDS[1])
    return {"p_stay_play": stay(play), "p_stay_dead": stay(dead),
            "prior_play": round(prior, 6)}


def viterbi_path(
    probs: np.ndarray,
    *,
    p_stay_play: float,
    p_stay_dead: float,
    prior_play: float,
) -> np.ndarray:
    """
    Most likely per-second in-play path of a two-state HMM (state 0 dead,
    1 play), decoded in log space.

    Emissions are the classifier's probabilities as they are: second t scores
    log p_t for play and log(1 − p_t) for dead, with p floored at
    _EMISSION_EPS from either end. `prior_play` is used only as the initial
    distribution — the first second's — not as a per-second weight. On an
    uninformative trace (every p equal) the path therefore starts in the
    likelier state and stays there when the stay probabilities agree with the
    prior, which `fit_transitions`' do: fitted from the same labels, the
    transitions' stationary share of play, Lp / (Lp + Ld) for mean run lengths
    Lp and Ld, is the in-play fraction whenever play and dead runs alternate —
    as they do within a game, up to one run per edge. A hand-set prior that
    favours the state with the SHORTER runs does not hold a long flat trace —
    staying in the other state is cheaper per second. Ties go to dead.

    Returns a bool array, one per input row.
    """
    p = np.clip(np.asarray(probs, dtype=np.float64), _EMISSION_EPS, 1.0 - _EMISSION_EPS)
    n = len(p)
    if n == 0:
        return np.zeros(0, dtype=bool)
    log_emit = np.column_stack((np.log1p(-p), np.log(p)))
    # log_trans[i, j] = log P(state j at t | state i at t-1)
    log_trans = np.log(np.array([
        [p_stay_dead, 1.0 - p_stay_dead],
        [1.0 - p_stay_play, p_stay_play],
    ]))
    delta = np.log(np.array([1.0 - prior_play, prior_play])) + log_emit[0]
    back = np.zeros((n, 2), dtype=np.int8)
    for t in range(1, n):
        cand = delta[:, None] + log_trans
        back[t] = np.argmax(cand, axis=0)
        delta = np.max(cand, axis=0) + log_emit[t]
    path = np.zeros(n, dtype=np.int8)
    path[-1] = int(np.argmax(delta))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    out: np.ndarray = path == 1
    return out


def viterbi_windows(
    probs: np.ndarray,
    duration: float,
    *,
    p_stay_play: float,
    p_stay_dead: float,
    prior_play: float,
    pad_before: float = 3.0,
    pad_after: float = 2.5,
    merge_gap_seconds: float = 5.0,
) -> list[Interval]:
    """
    `viterbi_path` over raw per-second probabilities → keep-windows, through the
    same pad/clamp/merge tail as `windows_from_probability`, with its defaults.

    Those pads and that merge gap were chosen for smooth-and-threshold, whose
    5s moving average already blurs every edge; this decoder inherits them
    unchanged rather than tuning its own.
    """
    if duration <= 0 or len(probs) == 0:
        return []
    keep = viterbi_path(probs, p_stay_play=p_stay_play, p_stay_dead=p_stay_dead,
                        prior_play=prior_play)
    return _runs_to_windows(keep, duration, pad_before=pad_before, pad_after=pad_after,
                            merge_gap_seconds=merge_gap_seconds)


def decode_windows(raw_probs: np.ndarray, model: dict, duration: float) -> list[Interval]:
    """
    Keep-windows from RAW per-second probabilities (`predict_in_play(...,
    smooth=False)`) by the decoder `model` names.

    "threshold" (or no `"decoder"` key) smooths them and thresholds at
    `model["threshold"]` — exactly the CF-393/CF-394 path. "hmm" decodes them
    unsmoothed with `model["hmm"]`. Anything else is refused (`check_decoder`).
    """
    check_decoder(model)
    if decoder_of(model) == "hmm":
        record = model["hmm"]
        return viterbi_windows(
            raw_probs, duration,
            p_stay_play=float(record["p_stay_play"]),
            p_stay_dead=float(record["p_stay_dead"]),
            prior_play=float(record["prior_play"]),
        )
    return windows_from_probability(smooth_probability(raw_probs),
                                    float(model["threshold"]), duration)


# ── the builder (CF-394) ───────────────────────────────────────────────────

def active_windows_learned(
    contacts: list[dict],
    positions: list[dict],
    duration: float,
    frame_height: int,
    *,
    weights: dict,
    allow_unvalidated: bool = False,
    min_track_rate: float = 1.0,
) -> list[Interval]:
    """
    Keep-windows from the learned in-play model: `compute_features` →
    `predict_in_play` (the weights' own scaling and context, unsmoothed) →
    `decode_windows` by the weights' `"decoder"` (CF-396). Under "threshold"
    — the committed file, and any file without the key — that is
    `windows_from_probability` on the smoothed trace at `"threshold"`, the
    same windows as before CF-396; under "hmm" it is `viterbi_windows`.

    **Raises on anything it cannot judge**, before computing anything, so the
    condense stage falls back to `guarded`:

      - `frame_height <= 0` or `duration <= 0` (ValueError) — every pixel column
        is divided by the height, and there is no second to score without a
        duration;
      - weights whose feature list, version or preparation record differ from
        this code's (ValueError, `check_contract`), or that carry no threshold;
      - `"validated"` anything but exactly `True`, unless `allow_unvalidated=True`
        (`UnvalidatedWeightsError`). Production never passes it; the eval
        ladder's `v6` does;
      - an empty track, or one below `min_track_rate` usable speed samples/s
        (`TrackTooSparseError`) — `track_is_usable`, the test
        `active_windows_guarded` abstains on, so on such a game the fallback
        abstains and the previous condensed cut is kept. Without this an empty
        track would score every second at its fills and return `[]`, a verdict
        the condense stage acts on by deleting the previous cut.

    `min_track_rate` defaults to `active_windows_guarded`'s; the condense stage
    passes `condense_guard_min_track_rate`, so both builders decline the same
    games.

    Returns a plain list. `[]` is a verdict ("nothing here is play"), not a
    failure, exactly as it is from `active_windows_guarded`: a caller must not
    read it as a reason to fall back.
    """
    if frame_height <= 0:
        raise ValueError(
            f"active_windows_learned needs a real frame height, got {frame_height!r} — "
            "every pixel feature is normalized by it"
        )
    if duration <= 0:
        raise ValueError(f"active_windows_learned needs a positive duration, got {duration!r}")
    check_contract(weights)
    if "threshold" not in weights:
        raise ValueError("deadtime weights carry no threshold — retrain")
    threshold = float(weights["threshold"])
    # Exactly True, not truthy: this is the production gate, and a hand-edited
    # or tool-written "false", "no" or 1 must not open it.
    if weights.get("validated") is not True and not allow_unvalidated:
        raise UnvalidatedWeightsError(
            "deadtime weights are not validated (\"validated\": false until "
            "CF-395 applies the epic's bar)"
        )
    if not positions:
        raise TrackTooSparseError("no ball track to score")
    samples = speed_samples(positions, frame_height)
    if not track_is_usable(samples, duration, min_rate=min_track_rate):
        raise TrackTooSparseError(
            f"{len(samples[0]) / duration:.2f} usable speed samples/s over "
            f"{duration:.0f}s is below {min_track_rate:.2f}/s"
        )

    features = compute_features(positions, contacts, duration, frame_height)
    raw = predict_in_play(features, weights, smooth=False)
    # Through the decoder the weights name (CF-396), never a fixed one: hmm
    # weights decoded by smooth-and-threshold would ship windows nobody scored.
    windows = decode_windows(raw, weights, duration)

    # Reporting must never break processing (CLAUDE.md → Code posture).
    try:
        if decoder_of(weights) == "hmm":
            logger.info(
                "Learned condense windows (hmm decoder, %s): %d s scored "
                "(peak %.3f) → %d windows keeping %.0fs",
                weights["hmm"], len(raw), float(np.max(raw)) if len(raw) else 0.0,
                len(windows), sum(e - s for s, e in windows),
            )
        else:
            probs = smooth_probability(raw)
            logger.info(
                "Learned condense windows: %d of %d s at or above threshold %.3f "
                "(peak %.3f) → %d windows keeping %.0fs",
                int(np.count_nonzero(probs >= threshold)), len(probs), threshold,
                float(np.max(probs)) if len(probs) else 0.0,
                len(windows), sum(e - s for s, e in windows),
            )
    except Exception:
        pass
    return windows
