"""
Audio energy analysis for detection confidence weighting.

Extracts the audio track from a video, computes RMS energy over time,
and uses it to boost detections near loud moments (cheering, ball hits,
whistles) and penalize detections during silence (dead time).

No extra dependencies — uses FFmpeg (subprocess) + numpy.
"""
from __future__ import annotations

import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000          # 16 kHz mono — enough for energy analysis
WINDOW_SEC = 0.5             # RMS window size in seconds
HOP_SEC = 0.1                # RMS hop size in seconds

# Energy percentile thresholds (computed per-video to normalize)
LOUD_PERCENTILE = 75         # Above this = "loud moment"
QUIET_PERCENTILE = 25        # Below this = "quiet moment"

# Confidence adjustments
LOUD_BOOST = 1.25            # Multiply confidence by this if near a loud moment
QUIET_PENALTY = 0.70         # Multiply confidence by this if in a quiet moment

# ── Cheer detection (post-rally crowd reaction) ──────────────────────────────
CHEER_WINDOW_SEC = 4.0       # How long after the final contact to listen for a reaction
CHEER_REF_PERCENTILE = 95    # Energy at this percentile maps to cheer_score = 1.0


def _extract_audio_pcm(video_path: str) -> np.ndarray | None:
    """
    Extract mono audio from a video file as raw float32 PCM using FFmpeg.
    Returns a 1-D numpy array of samples at SAMPLE_RATE Hz, or None on failure.
    """
    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vn",                       # no video
            "-ac", "1",                  # mono
            "-ar", str(SAMPLE_RATE),     # resample
            "-f", "f32le",               # raw float32 little-endian
            "-loglevel", "error",
            "pipe:1",                    # output to stdout
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning("FFmpeg audio extraction failed: %s", result.stderr[:200])
            return None

        samples = np.frombuffer(result.stdout, dtype=np.float32)
        if len(samples) == 0:
            logger.warning("No audio samples extracted from %s", video_path)
            return None

        logger.info("Extracted %d audio samples (%.1fs)", len(samples), len(samples) / SAMPLE_RATE)
        return samples

    except Exception:
        logger.warning("Audio extraction failed", exc_info=True)
        return None


def _compute_rms_energy(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute RMS energy in sliding windows.

    Returns:
        times: 1-D array of window center times (seconds)
        energy: 1-D array of RMS energy values
    """
    win_samples = int(WINDOW_SEC * SAMPLE_RATE)
    hop_samples = int(HOP_SEC * SAMPLE_RATE)

    n_windows = max(1, (len(samples) - win_samples) // hop_samples + 1)
    times = np.zeros(n_windows)
    energy = np.zeros(n_windows)

    for i in range(n_windows):
        start = i * hop_samples
        end = start + win_samples
        window = samples[start:end]
        times[i] = (start + end) / 2 / SAMPLE_RATE
        energy[i] = np.sqrt(np.mean(window ** 2))

    return times, energy


def _peak_energy_in_window(
    times: np.ndarray,
    energy: np.ndarray,
    start: float,
    end: float,
) -> float:
    """
    Return the maximum RMS energy within [start, end] seconds.

    We scan the full clip window rather than sampling around a midpoint so
    that rally clips (which can be 30–60 s long) are evaluated correctly.
    For single-action clips the window is just a few seconds, same effect.
    """
    mask = (times >= start) & (times <= end)
    if not np.any(mask):
        return 0.0
    return float(np.max(energy[mask]))


def compute_audio_energy(video_path: str) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Extract audio and compute the RMS energy envelope for a video.

    Returns (times, energy) arrays, or None if the video has no usable audio.
    Compute this once per video and pass it to weight_detections_by_audio()
    and score_cheers() to avoid re-extracting audio for each stage.
    """
    samples = _extract_audio_pcm(video_path)
    if samples is None:
        return None
    times, energy = _compute_rms_energy(samples)
    if len(energy) == 0:
        return None
    return times, energy


def score_cheers(
    detections: list[dict],
    times: np.ndarray,
    energy: np.ndarray,
) -> list[dict]:
    """
    Score each rally by the crowd/bench reaction AFTER it ends.

    Great plays produce cheering in the seconds following the final contact —
    ball-hit sounds during the rally do not discriminate (every rally has
    them), but the post-rally reaction does.

    Listens in [last_contact, last_contact + CHEER_WINDOW_SEC] (falls back to
    the clip end minus post-play padding when contact features are absent) and
    normalizes peak energy against the video's own levels:

        cheer = (peak - median) / (p95 - median), clipped to 0-1

    Writes the result to det["features"]["cheer_score"] and returns the list.
    """
    if not detections:
        return detections

    median_e = float(np.median(energy))
    ref_e = float(np.percentile(energy, CHEER_REF_PERCENTILE))
    spread = max(ref_e - median_e, 1e-9)

    result = []
    for det in detections:
        det = {**det, "features": dict(det.get("features", {}))}
        rally_end = det["features"].get("last_contact", det["end"])
        peak = _peak_energy_in_window(times, energy, rally_end, rally_end + CHEER_WINDOW_SEC)
        cheer = (peak - median_e) / spread
        det["features"]["cheer_score"] = round(float(np.clip(cheer, 0.0, 1.0)), 3)
        result.append(det)

    scores = [d["features"]["cheer_score"] for d in result]
    logger.info(
        "Cheer scoring: %d rallies, min=%.2f median=%.2f max=%.2f",
        len(scores), min(scores), float(np.median(scores)), max(scores),
    )
    return result


def weight_detections_by_audio(
    video_path: str,
    detections: list[dict],
    precomputed: tuple[np.ndarray, np.ndarray] | None = None,
) -> list[dict]:
    """
    Adjust detection confidence based on audio energy near the detection time.

    Loud moments (crowd cheering, ball hits) → boost confidence.
    Quiet moments (dead time, walking) → penalize confidence.

    Returns a new list of detections with adjusted confidence values.
    Detections are NOT removed — only their confidence is modified.

    Pass precomputed=(times, energy) from compute_audio_energy() to skip
    re-extracting the audio track.
    """
    if not detections:
        return detections

    if precomputed is not None:
        times, energy = precomputed
    else:
        computed = compute_audio_energy(video_path)
        if computed is None:
            logger.warning("No audio available — skipping audio weighting")
            return detections
        times, energy = computed

    # Compute thresholds relative to this video's audio levels
    loud_threshold = np.percentile(energy, LOUD_PERCENTILE)
    quiet_threshold = np.percentile(energy, QUIET_PERCENTILE)

    logger.info(
        "Audio energy: min=%.4f, median=%.4f, loud(p%d)=%.4f, quiet(p%d)=%.4f, max=%.4f",
        energy.min(), np.median(energy),
        LOUD_PERCENTILE, loud_threshold,
        QUIET_PERCENTILE, quiet_threshold,
        energy.max(),
    )

    result = []
    boosted = 0
    penalized = 0

    for det in detections:
        det = {**det}  # shallow copy
        # Scan the full clip window for the loudest moment.  The audio spike
        # (ball hit + crowd) lags the visible action by ~0.3–1 s, so it will
        # naturally fall inside the clip's PAD_AFTER tail — no offset needed.
        local_energy = _peak_energy_in_window(times, energy, det["start"], det["end"])

        if local_energy >= loud_threshold:
            det["confidence"] = min(det["confidence"] * LOUD_BOOST, 0.93)
            boosted += 1
        elif local_energy <= quiet_threshold:
            det["confidence"] = det["confidence"] * QUIET_PENALTY
            penalized += 1
        # else: confidence unchanged (mid-range energy)

        result.append(det)

    logger.info(
        "Audio weighting: %d boosted, %d penalized, %d unchanged",
        boosted, penalized, len(detections) - boosted - penalized,
    )
    return result
