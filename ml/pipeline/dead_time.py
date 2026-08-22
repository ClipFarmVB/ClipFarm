"""
Keep-window derivation for dead-time removal.

Turns signals the pipeline already computes (ball contacts, pose rally
windows) into the list of time windows worth keeping in a condensed video.
Everything between the windows is dead time and gets cut.

Unlike contacts_to_rallies() this is coverage-preserving, not curating:
no MAX_CLIP_DURATION subdivision (a long rally stays one span) and a looser
contact minimum, because dropping a real rally from a condensed game video
is much worse than including a marginal one.

Pure functions, no models, no I/O — tunables arrive as kwargs from the task.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

Interval = tuple[float, float]

# The tracking space speed_pxps defaults were tuned in. Keep in sync with
# ball.REFERENCE_FRAME_HEIGHT — asserted by test_reference_frame_height_matches_ball
# in ml/tests/test_dead_time.py. Not imported from there because ball.py imports
# numpy at module scope and this module stays dependency-light so the dead-time
# harness runs on a laptop. (The original reason given here was cv2, which CF-174
# made lazy; numpy is the one that remains.)
REFERENCE_FRAME_HEIGHT = 360.0


def merge_intervals(intervals: list[Interval], merge_gap_seconds: float = 0.0) -> list[Interval]:
    """Sort intervals and merge any pair closer than merge_gap_seconds."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[Interval] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= merge_gap_seconds:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _pad_and_clamp(
    intervals: list[Interval],
    duration: float,
    pad_before: float,
    pad_after: float,
) -> list[Interval]:
    return [
        (max(0.0, start - pad_before), min(duration, end + pad_after))
        for start, end in intervals
    ]


def active_windows_from_contacts(
    contacts: list[dict],
    duration: float,
    *,
    gap_seconds: float = 10.0,
    pad_before: float = 5.0,
    pad_after: float = 4.0,
    min_contacts: int = 1,
    merge_gap_seconds: float = 5.0,
) -> list[Interval]:
    """
    Group ball contacts (find_contacts() output, each with a "time" key) into
    active windows: a new window starts when the gap to the previous contact
    exceeds gap_seconds. Groups with fewer than min_contacts contacts are
    dropped; the default keeps every group, because at ~3fps sampling a real
    rally routinely surfaces as a single contact and losing it costs footage.

    Defaults are tuned loose on purpose (CF-46): keeping some dead time beats
    cutting play. pad_before must cover the full serve ritual — tracking often
    misses the serve contact itself, so the first detected contact is the
    receive, ~4-6s after the toss starts.
    """
    if not contacts:
        return []

    times = sorted(c["time"] for c in contacts)

    groups: list[list[float]] = [[times[0]]]
    for t in times[1:]:
        if t - groups[-1][-1] > gap_seconds:
            groups.append([t])
        else:
            groups[-1].append(t)

    windows = [(g[0], g[-1]) for g in groups if len(g) >= min_contacts]
    windows = _pad_and_clamp(windows, duration, pad_before, pad_after)
    merged = merge_intervals(windows, merge_gap_seconds)
    logger.info(
        "Condense windows from %d contacts: %d groups → %d windows",
        len(contacts), len(groups), len(merged),
    )
    return merged


def bridge_windows_by_motion(
    windows: list[Interval],
    positions: list[dict],
    *,
    speed_pxps: float = 150.0,
    fast_fraction: float = 0.35,
    max_bridge_seconds: float = 20.0,
    max_sample_spacing: float = 1.5,
    min_samples: int = 3,
    frame_height: int = 0,
) -> list[Interval]:
    """
    Merge adjacent windows when the tracked ball keeps moving fast through
    the gap between them (CF-46: fixes mid-rally cuts).

    Contact detection goes silent for long stretches of real play (far-court
    possessions, occlusions, smooth trajectories), splitting one rally into
    two windows. The ball track itself usually survives those stretches, and
    in-play flight is fast while between-rally handling (carrying, tossing a
    ball back) is mostly slow. So: bridge a gap only when at least
    fast_fraction of the speed samples inside it exceed speed_pxps.

    Guards against re-admitting dead time:
      - gaps longer than max_bridge_seconds never bridge (a between-games
        break with a few fast shag throws stays cut)
      - fewer than min_samples speed samples is no evidence — no bridge
      - presence alone never *creates* a window; this only joins windows
        already anchored by contacts

    positions are dicts with "time", "x", "y" (ball-track samples). Speeds
    are taken between consecutive samples closer than max_sample_spacing,
    so a tracking dropout contributes no samples rather than a huge jump.

    speed_pxps is in REFERENCE_FRAME_HEIGHT pixel space and is scaled to this
    video's frame_height (CF-174) — the same physical motion covers ~3x more
    pixels at 1080p, so an unscaled threshold calls ordinary handling "fast"
    and bridges gaps that are really dead time. frame_height <= 0 keeps the
    unscaled (360p) behaviour.
    """
    if len(windows) < 2 or not positions:
        return list(windows)

    if frame_height > 0:
        speed_pxps = speed_pxps * frame_height / REFERENCE_FRAME_HEIGHT
    else:
        # Same failure and same reasoning as ball._scale_for's warning: silently
        # applying a 360p threshold to a 1080p video is the bug this parameter
        # exists to prevent, and of the two call paths this is the one that
        # would otherwise never say so.
        logger.warning(
            "bridge_windows_by_motion called without frame_height — assuming a "
            "%.0fpx tracking space; speed_pxps=%.0f will read as 'fast' for "
            "ordinary ball handling on taller footage and over-bridge dead time",
            REFERENCE_FRAME_HEIGHT, speed_pxps,
        )

    pts = sorted((p["time"], p["x"], p["y"]) for p in positions)
    speeds: list[tuple[float, float]] = []  # (midpoint time, px/s)
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if 0 < dt <= max_sample_spacing:
            speeds.append(((t0 + t1) / 2, math.hypot(x1 - x0, y1 - y0) / dt))

    bridged: list[Interval] = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = bridged[-1]
        in_gap = [v for t, v in speeds if last_end < t < start]
        if (
            start - last_end <= max_bridge_seconds
            and len(in_gap) >= min_samples
            and sum(v >= speed_pxps for v in in_gap) / len(in_gap) >= fast_fraction
        ):
            bridged[-1] = (last_start, max(last_end, end))
        else:
            bridged.append((start, end))

    if len(bridged) < len(windows):
        logger.info(
            "Motion bridge: %d windows → %d (%d gaps bridged)",
            len(windows), len(bridged), len(windows) - len(bridged),
        )
    return bridged


def active_windows_from_detections(
    detections: list[dict],
    duration: float,
    *,
    pad_before: float = 5.0,
    pad_after: float = 4.0,
    merge_gap_seconds: float = 5.0,
) -> list[Interval]:
    """
    Fallback when the ball pipeline didn't run: derive windows from the
    pose-based rally dicts (group_into_rallies() output with start/end,
    which already carry per-action padding).
    """
    if not detections:
        return []

    windows = [(float(d["start"]), float(d["end"])) for d in detections]
    windows = _pad_and_clamp(windows, duration, pad_before, pad_after)
    merged = merge_intervals(windows, merge_gap_seconds)
    logger.info(
        "Condense windows from %d pose rallies → %d windows",
        len(detections), len(merged),
    )
    return merged
