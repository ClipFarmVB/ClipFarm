"""
Per-second in-play features for a learned dead-time builder (CF-392).

One row per whole second of the game, describing the ball track around that
second: how densely it is tracked, how fast the ball is moving, how close the
nearest contacts are, and where in the frame the ball sits. A learned builder
scores those rows; this module only produces them.

**Scope.** Features and the weights contract, nothing else. Training, the
weights file itself, and window building are separate cards (CF-393, CF-394) —
so there is deliberately no `predict_in_play` and no `active_windows_from_ml`
here yet, and nothing imports `ml.pipeline.intervals`.

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

The module imports only numpy and `ml.pipeline.dead_time` — `ml/tests` runs with
numpy alone in CI, and `ml.pipeline.ball` imports cv2.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np

from ml.pipeline.dead_time import speed_samples

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
FEATURE_VERSION = 3

# The per-sample "this is fast" bar, in frame-heights/s.
#
# A FOURTH copy of 0.30, and the count matters because each one is a place it
# can drift. The others: `motion_anchor_windows(speed=...)`'s default, which
# actually applies it; `active_windows_guarded(anchor_speed=...)`, which passes
# it down; and `condense_guard_anchor_speed` in api/app/config.py, which is what
# PRODUCTION passes (tasks.py), and which is unreachable from here since
# ml/pipeline must not import app.config.
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

    # `confidence` is optional in this dict shape and EVERY producer in this
    # repository currently omits it. FIVE sites, not the four this comment
    # listed until a round counted them, and they are not all the same shape:
    #
    #   api/app/workers/tasks.py:1180      build {"time","x","y"} from a
    #   ml/eval/harness.py:613             BallPosition that HAS the field
    #   ml/eval/diagnose_detection.py:168
    #
    #   ml/eval/deadtime_variants.py:83    re-serialize a dict that already
    #   ml/eval/tune_contacts.py:80        lost it upstream
    #
    # So mean_conf_3s and max_conf_3s are constant 0.0 on real input, which is
    # also their empty-window fill: "no confidence supplied" and "no samples at
    # all" are indistinguishable in the matrix.
    #
    # Two of thirteen columns training as constants is worth a line in the log
    # rather than a silent zero, because FEATURE_VERSION is frozen here and the
    # trainer (CF-393) reads whatever this produces. Forwarding it is one line
    # in the three that hold a BallPosition; the other two only carry what the
    # dump they read already has, so diagnose_detection's dump format decides
    # tune_contacts. That is CF-420 (#544), and tasks.py is out of this card's
    # scope either way.
    if positions and not any("confidence" in p for p in positions):
        logger.warning(
            "no ball-track sample carries 'confidence' — mean_conf_3s and "
            "max_conf_3s will be constant 0.0, indistinguishable from an "
            "unsampled second. Producers drop the field; see CF-392."
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


def load_weights(path: Path = WEIGHTS_PATH) -> dict:
    """
    Load a weights file, validating the feature contract.

    **There is no committed weights file yet** — training is CF-393 — so calling
    this with no argument raises FileNotFoundError on current main. That is
    honest rather than convenient: the contract check is what CF-393's output
    has to satisfy, and it is worth having before there is anything to check.

    A weights file carries "validated": true only once its leave-one-game-out
    numbers justify enabling it. Unvalidated weights still load, so the flag
    stays usable for experiments, but warn.
    """
    data = json.loads(path.read_text())
    if data.get("features") != FEATURE_NAMES:
        raise ValueError(
            f"deadtime weights at {path} were trained for features "
            f"{data.get('features')}, code expects {FEATURE_NAMES}"
        )
    if data.get("feature_version") != FEATURE_VERSION:
        raise ValueError(
            f"deadtime weights at {path} are feature_version "
            f"{data.get('feature_version')}, code expects {FEATURE_VERSION} — retrain"
        )
    if not data.get("validated"):
        logger.warning(
            "deadtime weights at %s are NOT validated (trained on %s) — "
            "keep-windows from them are unproven on unseen footage",
            path, data.get("trained_on", "?"),
        )
    return data
