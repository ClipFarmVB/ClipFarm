"""
Learned keep-window derivation for dead-time removal (ML alternative to the
threshold rules in dead_time.py).

A tiny logistic model scores every second of the game as in-play vs dead from
features of the ball track (sample density, speed, contact rhythm). The
per-second probabilities are smoothed, thresholded, and turned into padded
keep-windows with the same coverage-preserving posture as
active_windows_from_contacts(): dropping real play is much worse than keeping
some dead time, so the shipped threshold is tuned recall-heavy.

Training lives outside the repo runtime (see the CF card): weights are fitted
against the hand-labeled in-play spans in ml/eval fixtures and committed as
deadtime_ml_weights.json next to this file. Inference is pure numpy — no
sklearn, no model download, no I/O beyond reading that one committed file.

Like dead_time.py this module is pure functions; the condense stage wraps the
call in try/except and falls back to the rule-based windows on any failure.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np

from ml.pipeline.dead_time import Interval, merge_intervals

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).with_name("deadtime_ml_weights.json")

# Order matters: must match the "features" list in the committed weights file.
FEATURE_NAMES = [
    "track_rate_2s",      # ball-track samples per second within ±2s
    "track_coverage_10s",  # fraction of ±10s one-second bins with any sample
    "mean_speed_3s",      # mean ball speed (px/s) within ±3s
    "fast_fraction_5s",   # fraction of ±5s speed samples ≥ 150 px/s (in-play flight)
    "contacts_5s",        # contacts within ±5s
    "contacts_15s",       # contacts within ±15s
    "since_contact",      # seconds since previous contact, capped
    "until_contact",      # seconds until next contact, capped
    "mean_conf_3s",       # mean tracker confidence within ±3s
    "y_std_5s",           # std of ball y within ±5s (flight vs. carried ball)
]

FAST_SPEED_PXPS = 150.0     # same in-play flight threshold as the motion bridge
MAX_SAMPLE_SPACING = 1.5    # same dropout guard as bridge_windows_by_motion
CONTACT_GAP_CAP = 60.0      # since/until saturate here — beyond this it's just "long ago"
SMOOTH_SECONDS = 5          # moving-average width for the probability trace


def compute_features(
    positions: list[dict],
    contacts: list[dict],
    duration: float,
) -> np.ndarray:
    """
    Per-second feature matrix, one row per whole second of [0, duration).

    positions are ball-track samples ({"time", "x", "y", "confidence"?}),
    contacts are find_contacts() output ({"time", ...}). Both may be empty:
    a second with no nearby track data gets zero activity features and
    saturated contact gaps, which the model reads as dead time.
    """
    n = max(1, int(math.ceil(duration)))
    centers = np.arange(n, dtype=np.float64) + 0.5

    pts = sorted((p["time"], p["x"], p["y"], p.get("confidence", 0.0)) for p in positions)
    p_times = np.array([p[0] for p in pts], dtype=np.float64)
    p_y = np.array([p[2] for p in pts], dtype=np.float64)
    p_conf = np.array([p[3] for p in pts], dtype=np.float64)

    # Speeds between consecutive samples, skipping tracking dropouts so a gap
    # contributes no samples rather than one huge jump (as in the motion bridge).
    s_times: list[float] = []
    s_values: list[float] = []
    for (t0, x0, y0, _), (t1, x1, y1, _) in zip(pts, pts[1:]):
        dt = t1 - t0
        if 0 < dt <= MAX_SAMPLE_SPACING:
            s_times.append((t0 + t1) / 2)
            s_values.append(math.hypot(x1 - x0, y1 - y0) / dt)
    sp_times = np.array(s_times, dtype=np.float64)
    sp_values = np.array(s_values, dtype=np.float64)

    c_times = np.array(sorted(c["time"] for c in contacts), dtype=np.float64)

    # Which one-second bins contain any track sample (for coverage).
    bin_has_sample = np.zeros(n, dtype=bool)
    if len(p_times):
        idx = np.clip(p_times.astype(int), 0, n - 1)
        bin_has_sample[idx] = True
    coverage_cum = np.concatenate(([0], np.cumsum(bin_has_sample)))

    def _window_slice(sorted_times: np.ndarray, lo: np.ndarray, hi: np.ndarray):
        return np.searchsorted(sorted_times, lo), np.searchsorted(sorted_times, hi)

    def _window_mean(sorted_times: np.ndarray, values: np.ndarray,
                     half: float, default: float = 0.0) -> np.ndarray:
        lo, hi = _window_slice(sorted_times, centers - half, centers + half)
        cum = np.concatenate(([0.0], np.cumsum(values)))
        count = (hi - lo).astype(np.float64)
        out = np.full(n, default, dtype=np.float64)
        nz = count > 0
        out[nz] = (cum[hi] - cum[lo])[nz] / count[nz]
        return out

    feats = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float64)

    lo, hi = _window_slice(p_times, centers - 2.0, centers + 2.0)
    feats[:, 0] = (hi - lo) / 4.0

    b_lo = np.clip((centers - 10.0).astype(int), 0, n)
    b_hi = np.clip((centers + 10.0).astype(int) + 1, 0, n)
    feats[:, 1] = (coverage_cum[b_hi] - coverage_cum[b_lo]) / np.maximum(b_hi - b_lo, 1)

    feats[:, 2] = _window_mean(sp_times, sp_values, 3.0)
    feats[:, 3] = _window_mean(sp_times, (sp_values >= FAST_SPEED_PXPS).astype(np.float64), 5.0)

    lo, hi = _window_slice(c_times, centers - 5.0, centers + 5.0)
    feats[:, 4] = hi - lo
    lo, hi = _window_slice(c_times, centers - 15.0, centers + 15.0)
    feats[:, 5] = hi - lo

    if len(c_times):
        prev_idx = np.searchsorted(c_times, centers, side="right") - 1
        has_prev = prev_idx >= 0
        feats[:, 6] = CONTACT_GAP_CAP
        feats[has_prev, 6] = np.minimum(centers[has_prev] - c_times[prev_idx[has_prev]], CONTACT_GAP_CAP)
        next_idx = np.searchsorted(c_times, centers, side="left")
        has_next = next_idx < len(c_times)
        feats[:, 7] = CONTACT_GAP_CAP
        feats[has_next, 7] = np.minimum(c_times[next_idx[has_next]] - centers[has_next], CONTACT_GAP_CAP)
    else:
        feats[:, 6] = CONTACT_GAP_CAP
        feats[:, 7] = CONTACT_GAP_CAP

    feats[:, 8] = _window_mean(p_times, p_conf, 3.0)

    # y-std within ±5s: E[y²] − E[y]² on the same windowed sums.
    mean_y = _window_mean(p_times, p_y, 5.0)
    mean_y2 = _window_mean(p_times, p_y * p_y, 5.0)
    feats[:, 9] = np.sqrt(np.maximum(mean_y2 - mean_y * mean_y, 0.0))

    return feats


def load_weights(path: Path = WEIGHTS_PATH) -> dict:
    """Load the committed weights file, validating the feature contract."""
    data = json.loads(path.read_text())
    if data.get("features") != FEATURE_NAMES:
        raise ValueError(
            f"deadtime weights at {path} were trained for features "
            f"{data.get('features')}, code expects {FEATURE_NAMES}"
        )
    return data


def predict_in_play(features: np.ndarray, weights: dict) -> np.ndarray:
    """Per-second P(in-play), smoothed over SMOOTH_SECONDS."""
    mean = np.array(weights["mean"], dtype=np.float64)
    std = np.array(weights["std"], dtype=np.float64)
    coef = np.array(weights["coef"], dtype=np.float64)
    z = (features - mean) / np.where(std > 0, std, 1.0)
    logits = z @ coef + weights["intercept"]
    probs = 1.0 / (1.0 + np.exp(-logits))
    kernel = np.ones(SMOOTH_SECONDS) / SMOOTH_SECONDS
    return np.convolve(probs, kernel, mode="same")


def active_windows_from_ml(
    positions: list[dict],
    contacts: list[dict],
    duration: float,
    *,
    weights: dict | None = None,
    pad_before: float = 3.0,
    pad_after: float = 2.5,
    merge_gap_seconds: float = 5.0,
) -> list[Interval]:
    """
    Learned counterpart to active_windows_from_contacts() +
    bridge_windows_by_motion(): per-second in-play probabilities → thresholded
    spans → padded, merged keep-windows.

    The decision threshold ships inside the weights file — it was chosen during
    training against the same labels as the coefficients, so overriding one
    without the other silently changes the recall/aggressiveness trade-off.

    Pads default tighter than the condense_pad_* settings on purpose: those pad
    sparse contact anchors (where the first detection is often the receive),
    while these pad spans that already cover the detected play, so the rule-based
    pads would double-count. The threshold was tuned against these pads.

    Raises on a missing/mismatched weights file; the condense stage catches and
    falls back to the rule-based windows.
    """
    if duration <= 0 or (not positions and not contacts):
        return []

    w = weights if weights is not None else load_weights()
    probs = predict_in_play(compute_features(positions, contacts, duration), w)
    keep = probs >= w["threshold"]

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

    windows = [
        (max(0.0, s - pad_before), min(duration, e + pad_after)) for s, e in windows
    ]
    merged = merge_intervals(windows, merge_gap_seconds)
    logger.info(
        "ML condense windows: %d in-play seconds → %d windows",
        int(keep.sum()), len(merged),
    )
    return merged
