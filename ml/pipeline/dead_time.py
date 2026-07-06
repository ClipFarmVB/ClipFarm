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

logger = logging.getLogger(__name__)

Interval = tuple[float, float]


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
    gap_seconds: float = 5.0,
    pad_before: float = 2.0,
    pad_after: float = 2.5,
    min_contacts: int = 2,
    merge_gap_seconds: float = 1.5,
) -> list[Interval]:
    """
    Group ball contacts (find_contacts() output, each with a "time" key) into
    active windows: a new window starts when the gap to the previous contact
    exceeds gap_seconds. Groups with fewer than min_contacts contacts are
    noise (a stray detection between rallies), not play.
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


def active_windows_from_detections(
    detections: list[dict],
    duration: float,
    *,
    pad_before: float = 2.0,
    pad_after: float = 2.5,
    merge_gap_seconds: float = 1.5,
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
