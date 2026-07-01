"""
Highlight scoring for rally clips.

Combines label-free signals into a single highlight_score (0-1) per rally:

  1. Cheer score — crowd/bench reaction after the rally ends (audio.py).
     The strongest signal: great plays get audible reactions.
  2. Rally shape — contact count, duration, max ball speed, sharp direction
     changes, floor bounce. Computed for free by ball.py during tracking.
  3. CLIP score (optional) — zero-shot action-vs-idle classification on
     several frames sampled across the rally (verify.py).

No training data or labeling required. Weights are module constants so they
can be tuned as real usage data accumulates.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# ── Component weights (renormalized when CLIP is unavailable) ────────────────
WEIGHT_CHEER = 0.45
WEIGHT_SHAPE = 0.35
WEIGHT_CLIP  = 0.20

# ── Rally shape normalization ────────────────────────────────────────────────
CONTACTS_SATURATION = 8      # contact_count at which the sub-score maxes out
SPEED_SATURATION    = 0.04   # max_speed (frame-heights/frame) for full score
SHARP_SATURATION    = 4      # sharp direction changes for full score
DURATION_MIN  = 4.0          # rallies shorter than this score 0 on duration
DURATION_LOW  = 8.0          # full duration score from here...
DURATION_HIGH = 25.0         # ...to here
DURATION_MAX  = 35.0         # rallies longer than this taper back to 0.5
FLOOR_BOUNCE_BONUS = 0.15    # emphatic point endings (kill / diving dig)

# ── Multi-frame CLIP sampling ────────────────────────────────────────────────
CLIP_FRAMES_PER_RALLY = 4


def _duration_score(duration: float) -> float:
    """Trapezoid: too-short rallies are noise, very long ones drag."""
    if duration <= DURATION_MIN:
        return 0.0
    if duration < DURATION_LOW:
        return (duration - DURATION_MIN) / (DURATION_LOW - DURATION_MIN)
    if duration <= DURATION_HIGH:
        return 1.0
    if duration >= DURATION_MAX:
        return 0.5
    return 1.0 - 0.5 * (duration - DURATION_HIGH) / (DURATION_MAX - DURATION_HIGH)


def _shape_score(features: dict) -> float:
    """Combine rally trajectory features into a 0-1 'watchability' score."""
    contacts = min(features.get("contact_count", 0) / CONTACTS_SATURATION, 1.0)
    duration = _duration_score(features.get("duration", 0.0))
    speed    = min(features.get("max_speed", 0.0) / SPEED_SATURATION, 1.0)
    sharp    = min(features.get("sharp_changes", 0) / SHARP_SATURATION, 1.0)

    score = 0.35 * contacts + 0.30 * duration + 0.20 * speed + 0.15 * sharp
    if features.get("floor_bounce"):
        score += FLOOR_BOUNCE_BONUS
    return float(min(score, 1.0))


def _clip_scores(video_path: str, detections: list[dict]) -> list[float | None]:
    """
    Zero-shot CLIP action score per rally: sample frames evenly across the
    rally window and take the max action score. Returns None entries when
    CLIP or a frame is unavailable.
    """
    try:
        from ml.pipeline.verify import _extract_frame, score_frame, _get_clip
        _get_clip()
    except Exception:
        logger.warning("CLIP unavailable — scoring without visual component")
        return [None] * len(detections)

    scores: list[float | None] = []
    for det in detections:
        span = det["end"] - det["start"]
        # Sample interior timestamps, avoiding the padded edges
        ts = [det["start"] + span * (i + 1) / (CLIP_FRAMES_PER_RALLY + 1)
              for i in range(CLIP_FRAMES_PER_RALLY)]
        best: float | None = None
        for t in ts:
            frame = _extract_frame(video_path, t)
            if frame is None:
                continue
            action_score, _ = score_frame(frame)
            best = action_score if best is None else max(best, action_score)
        scores.append(best)
    return scores


def score_highlights(
    video_path: str,
    detections: list[dict],
    use_clip: bool = True,
) -> list[dict]:
    """
    Compute det["highlight_score"] (0-1) for each rally detection.

    Expects det["features"] populated by ball.py (rally shape) and audio.py
    (cheer_score); missing features degrade gracefully to 0. Returns a new
    list — input dicts are not mutated.
    """
    if not detections:
        return detections

    clip_scores = _clip_scores(video_path, detections) if use_clip else [None] * len(detections)

    result = []
    for det, clip_s in zip(detections, clip_scores):
        features = det.get("features", {})
        cheer = float(features.get("cheer_score", 0.0))
        shape = _shape_score(features)

        if clip_s is None:
            # Redistribute CLIP's weight proportionally across the other two
            total_w = WEIGHT_CHEER + WEIGHT_SHAPE
            score = (WEIGHT_CHEER * cheer + WEIGHT_SHAPE * shape) / total_w
        else:
            score = WEIGHT_CHEER * cheer + WEIGHT_SHAPE * shape + WEIGHT_CLIP * clip_s

        det = {**det, "highlight_score": round(float(np.clip(score, 0.0, 1.0)), 3)}
        result.append(det)

    scores = [d["highlight_score"] for d in result]
    logger.info(
        "Highlight scoring: %d rallies, min=%.2f median=%.2f max=%.2f",
        len(scores), min(scores), float(np.median(scores)), max(scores),
    )
    return result
