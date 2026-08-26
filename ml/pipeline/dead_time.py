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

Two keep-window builders ship here (`condense_mode` picks between them):

  "rules"    active_windows_from_contacts + bridge_windows_by_motion (CF-46)
  "guarded"  active_windows_guarded (CF-187) — the default: contacts are
             speed-gated, sustained motion opens windows of its own, pads are
             tight, and a track too sparse to judge abstains instead of guessing
"""
from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

Interval = tuple[float, float]


class Abstained(list):
    """The whole-video window returned when the track is too sparse to judge.

    A plain `[(0.0, duration)]` says *what* the builder returned and loses *why*,
    so every caller that needs the difference has to infer it back from the
    shape — `_worth_condensing` by a near-total-keep heuristic, the eval harness
    by comparing against `[(0.0, duration)]`, which also matches a genuine
    full-coverage condense. This is a `list` subclass rather than a new return
    type on purpose: it *is* the window list, so callers that only need the
    windows keep working untouched, and the one fact they were guessing at is
    available as `isinstance(windows, Abstained)`.

    An abstain is not an error and not an empty verdict. Both of those already
    have their own shapes here: `[]` means "nothing in this video is play", and
    an exception falls back to the rules path.
    """

# ── guarded-path constants (CF-187) ────────────────────────────────────────
# Speeds here are in frame-heights/s, not px/s, so 360p and 1080p games share
# one set of thresholds. The px/s thresholds above predate this and stay px/s
# to avoid rescaling a tuned path that is still reachable via
# condense_mode="rules".

MAX_SAMPLE_SPACING = 1.5   # a longer gap is a tracking dropout, not motion

# Displacement faster than this is usually the tracker hopping between two
# different objects rather than one ball flying, so its *magnitude* means
# nothing. It is clamped to this ceiling rather than discarded: 32-63% of the
# samples above it (measured per fixture) fall inside labeled play, so dropping
# them throws away real rally evidence and, because the abstain guard counts
# samples, penalises exactly the fast well-tracked games it should trust.
# Clamping keeps the sample's existence — which is what the density and
# fast-fraction tests read — while refusing to believe its size. Matches
# ball.py's SEG_MAX_SPEED_PXPS (1200 px/s = 1.11 frame-heights/s at 1080p).
#
# That equivalence holds *at 1080p only*, and the "at 1080p" is not a footnote:
# in px/s this ceiling scales with the source, so on 360p footage it sits at
# 400 px/s — 3x stricter than the tracker's own cap. It bites accordingly:
# 226 of test1's 5636 usable samples (4.0%) exceed it against 2 under a literal
# px/s cap. That asymmetry costs nothing today *because* this clamps rather than
# drops — re-running the fixtures with this cap at the px/s equivalent, or removed
# entirely, gives byte-identical windows and nets on all five. Restore any form of
# dropping here and the resolution dependence becomes load-bearing again.
MAX_PLAUSIBLE_SPEED_FH = 1.11

CONTACT_SPEED_HALF_WINDOW = 1.5   # median speed is taken over ±this around a contact

ANCHOR_HALF_WINDOW = 3.0
ANCHOR_MIN_FRACTION = 0.4
ANCHOR_PAD = 2.0
ANCHOR_MIN_SECONDS = 2.0
# Below this many speed samples in the window the fast-fraction is noise, not
# evidence — test2 tracks at 1.5 samples/s, so a couple of stray fast samples
# would otherwise open a window over dead time. Same guard as
# bridge_windows_by_motion's min_samples.
ANCHOR_MIN_SAMPLES = 6

SpeedSamples = tuple[np.ndarray, np.ndarray]   # (midpoint times, speeds in fh/s)


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
    """
    if len(windows) < 2 or not positions:
        return list(windows)

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


# ── guarded path (CF-187) ──────────────────────────────────────────────────

def speed_samples(
    positions: list[dict],
    frame_height: int,
    *,
    max_sample_spacing: float = MAX_SAMPLE_SPACING,
    max_speed: float = MAX_PLAUSIBLE_SPEED_FH,
) -> SpeedSamples:
    """
    Ball speeds between consecutive track samples, in frame-heights/s.

    Pairs further apart than max_sample_spacing are dropped rather than measured:
    across a tracking dropout the displacement says nothing about how fast the
    ball moved. Speeds above max_speed are *clamped* instead — the tracker
    jumping between two objects still tells us a sample exists at that instant,
    and discarding it would both lose real rally evidence and thin the sample
    count the abstain guard counts.

    Returns sorted (times, speeds); empty arrays when there is nothing usable.
    """
    if frame_height <= 0:
        return np.empty(0), np.empty(0)

    pts = sorted((p["time"], p["x"], p["y"]) for p in positions)
    times: list[float] = []
    vals: list[float] = []
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if not 0 < dt <= max_sample_spacing:
            continue
        v = math.hypot(x1 - x0, y1 - y0) / dt / frame_height
        # Clamped, not dropped — see MAX_PLAUSIBLE_SPEED_FH. A track hop still
        # happened at this instant; only its size is untrustworthy.
        times.append((t0 + t1) / 2)
        vals.append(min(v, max_speed))
    return np.array(times), np.array(vals)


def track_is_usable(samples: SpeedSamples, duration: float, *, min_rate: float = 1.0) -> bool:
    """
    Whether the ball track is dense enough for any of this to mean anything.

    Below min_rate **usable speed samples** per second there is no basis for a
    judgement about play. The unit matters and is easy to get wrong: this counts
    the output of speed_samples(), which is pairs of track points close enough
    together to measure — not raw track points. On fixture test3 those are 0.57/s
    and 0.76/s respectively, and its own fixture note records the raw figure, so
    the two numbers describe the same game and are not interchangeable. Against
    min_rate only the usable rate is meaningful.

    test3 runs at 0.57 usable/s (vs 1.51-2.99 on the other four) and 21 of its 32
    rallies produce no contact at all, so every builder — the rule-based one
    included — cuts more than half its live play. The density also sits under
    what ANCHOR_MIN_SAMPLES needs, so the motion anchor cannot recover those
    rallies either.
    """
    times, _ = samples
    return duration > 0 and len(times) / duration >= min_rate


def speed_gate_contacts(
    contacts: list[dict],
    samples: SpeedSamples,
    *,
    min_speed: float = 0.25,
    half_window: float = CONTACT_SPEED_HALF_WINDOW,
) -> list[dict]:
    """
    Drop contacts whose surrounding ball motion is too slow to be real play.

    A contact is a bend in the trajectory; on a track locked to a stationary
    spare ball, detector jitter produces bends indistinguishable from hits. On
    the tuning fixtures 24-48% of contacts fire during dead time, and those are
    the dominant false-positive class. Requiring motion around the contact
    separates the two without touching find_contacts' own thresholds.

    With no speed samples there is nothing to gate on, so every contact stands.
    That holds *locally* as well as globally: a contact with no usable sample
    within half_window is unjudged, not rejected. The abstain guard cannot cover
    this case, because it tests the whole-video average rate — a track that is
    dense overall but drops out for one rally passes the guard and would then
    lose that rally's contacts to a gate that never measured them. Absence of
    evidence is not evidence of a stationary ball.
    """
    times, speeds = samples
    if not len(times):
        return list(contacts)

    kept = []
    for c in contacts:
        lo, hi = np.searchsorted(times, [c["time"] - half_window, c["time"] + half_window])
        if hi <= lo or float(np.median(speeds[lo:hi])) >= min_speed:
            kept.append(c)
    return kept


def motion_anchor_windows(
    samples: SpeedSamples,
    duration: float,
    *,
    speed: float = 0.30,
    half_window: float = ANCHOR_HALF_WINDOW,
    min_fraction: float = ANCHOR_MIN_FRACTION,
    pad: float = ANCHOR_PAD,
    min_seconds: float = ANCHOR_MIN_SECONDS,
    min_samples: int = ANCHOR_MIN_SAMPLES,
) -> list[Interval]:
    """
    Windows from sustained fast ball motion, independent of contacts.

    Contacts are the only thing that can *open* a window in the rule-based path,
    so a rally the detector never sees is cut outright — 19 of fixture test4's
    46 rallies produce no contact at all, the single largest source of removed
    live play. Rally flight is fast and sustained; between-rally handling is not.

    Unlike bridge_windows_by_motion this creates windows rather than joining
    them, so the evidence bar is higher: a full second must sit inside a
    ±half_window stretch that is min_fraction fast over at least min_samples
    samples, and the resulting run must last min_seconds.
    """
    times, speeds = samples
    if not len(times) or duration <= 0:
        return []

    n = max(1, int(math.ceil(duration)))
    centers = np.arange(n) + 0.5
    lo = np.searchsorted(times, centers - half_window)
    hi = np.searchsorted(times, centers + half_window)
    cum = np.concatenate(([0.0], np.cumsum((speeds >= speed).astype(float))))
    count = (hi - lo).astype(float)
    frac = np.where(count > 0, (cum[hi] - cum[lo]) / np.maximum(count, 1), 0.0)
    on_mask = (frac >= min_fraction) & (count >= min_samples)

    windows: list[Interval] = []
    start: int | None = None
    for i, on in enumerate(on_mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            windows.append((float(start), float(i)))
            start = None
    if start is not None:
        windows.append((float(start), float(n)))

    return [
        (max(0.0, s - pad), min(duration, e + pad))
        for s, e in windows
        if e - s >= min_seconds
    ]


def active_windows_guarded(
    contacts: list[dict],
    positions: list[dict],
    duration: float,
    frame_height: int,
    *,
    gap_seconds: float = 10.0,
    min_contacts: int = 1,
    pad_before: float = 3.0,
    pad_after: float = 2.0,
    merge_gap_seconds: float = 3.0,
    gate_speed: float = 0.25,
    anchor_speed: float = 0.30,
    anchor_pad: float = ANCHOR_PAD,
    min_track_rate: float = 1.0,
) -> list[Interval]:
    """
    Keep-windows from speed-gated contacts plus motion anchors, or the whole
    video when the ball track is too sparse to judge (CF-187's v5).

    Three changes from the rule-based path, each measured on the dead-time
    fixtures and none safe alone:

      - contacts fired over a near-stationary ball are rejected (speed_gate)
      - sustained motion opens its own windows, so a rally the contact detector
        misses is no longer cut outright (motion_anchor)
      - pads shrink to 3.0/2.0 with merge 3.0. pad_before + pad_after +
        merge_gap is the gap a real break must exceed to survive, and the median
        inter-rally gap is 13-14s against the rule-based path's 14s budget — so
        half of all true dead gaps were being erased by arithmetic before
        detection quality mattered. Only safe once the anchor has given every
        rally its own window.

    pad_before/pad_after apply to the contact-derived windows only; anchor
    windows carry their own `anchor_pad` and do **not** follow them. The two pad
    different things: a contact window is anchored at points and has to reach
    back over the untracked serve, while an anchor window already spans the
    motion it found, so the contact pads would double-count it. Raising
    pad_before therefore has no effect on a rally recovered purely by the anchor.

    Abstaining returns a single whole-video window: on a track that thin every
    builder buys dead time by cutting play, so declining to condense is both the
    better score and the honest answer. The caller sees a normal window list and
    ships an uncondensed video.

    `min_track_rate` says *that* an abstain region exists; it does not say where
    its edge belongs. In usable samples/s the fixtures sit at 0.57 (the game that
    must abstain) and 1.51-2.99 (the four that must not), so the default 1.0
    stands in a wide clean gap with nothing observed inside it. Treat the boundary
    as unconstrained rather than tuned — and note the evidence is one game, which
    is also the game excluded from cross-game comparison (see
    EXCLUDED_FROM_TOTALS in ml/eval/visualize_deadtime.py). On the comparable
    fixtures the abstain never fires, so v4 and v5 score identically there: this
    switch is supported by a single excluded fixture, and a per-region degrade
    would be a better shape than an all-or-nothing flip.

    Raises on a missing frame height rather than abstaining on one: every speed
    here is normalized by it, so a zero would silently turn every game into an
    abstain. The condense stage catches and falls back to the rule-based windows,
    which need no frame height at all.
    """
    if frame_height <= 0:
        raise ValueError(
            f"active_windows_guarded needs a real frame height, got {frame_height!r} — "
            "every speed threshold is normalized by it"
        )
    if duration <= 0:
        return []

    samples = speed_samples(positions, frame_height)
    if not track_is_usable(samples, duration, min_rate=min_track_rate):
        times, _ = samples
        logger.info(
            "Condense abstain: %.2f usable speed samples/s over %.0fs is below "
            "%.2f/s — keeping the whole video rather than condensing on a track "
            "this sparse",
            len(times) / duration if duration else 0.0, duration, min_track_rate,
        )
        # Abstained, not a plain list: the caller decides what to do with the
        # whole-video window, but it should not have to re-derive *why* it got
        # one. See the class docstring.
        return Abstained([(0.0, duration)])

    gated = speed_gate_contacts(contacts, samples, min_speed=gate_speed)
    windows = active_windows_from_contacts(
        gated, duration,
        gap_seconds=gap_seconds,
        pad_before=pad_before,
        pad_after=pad_after,
        min_contacts=min_contacts,
        merge_gap_seconds=merge_gap_seconds,
    )
    anchors = motion_anchor_windows(samples, duration, speed=anchor_speed, pad=anchor_pad)
    merged = merge_intervals(windows + anchors, merge_gap_seconds)
    logger.info(
        "Guarded condense windows: %d contacts → %d after speed gate, "
        "+%d motion anchors → %d windows",
        len(contacts), len(gated), len(anchors), len(merged),
    )
    return merged


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
