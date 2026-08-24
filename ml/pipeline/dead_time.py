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

# ── guarded-path constants (CF-187) ────────────────────────────────────────
# Speeds here are in frame-heights/s, not px/s, so 360p and 1080p games share
# one set of thresholds. The px/s thresholds above predate this and stay px/s
# to avoid rescaling a tuned path that is still reachable via
# condense_mode="rules".

MAX_SAMPLE_SPACING = 1.5   # a longer gap is a tracking dropout, not motion

# Displacement faster than this is the tracker hopping between two different
# objects, not one ball flying: measured on the dead-time fixtures, samples
# above it land inside labeled play only 33-53% of the time, *worse* than the
# 0.40-1.10 band (58-77%). Treating them as motion is what makes a between-rally
# stretch of spare-ball flicker look like a rally. Matches ball.py's
# SEG_MAX_SPEED_PXPS (1200 px/s = 1.11 frame-heights/s at 1080p).
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

# ── pose-signal constants (CF-198) ─────────────────────────────────────────
# Player activity is normalized by frame height too, so it shares the
# frame-heights/s unit with the ball speeds above and the same thresholds work
# at 360p and 1080p.

# COCO-17 wrist indices, duplicated from detect.py rather than imported: that
# module imports cv2 at module scope, and this one is deliberately dependency-
# free so the condense stage can build windows without a vision runtime.
L_WRIST, R_WRIST = 9, 10

# Wrists only, following detect.py's own MOTION_THRESHOLD gate. Ankles were the
# obvious addition and are the wrong one: walking between rallies moves ankles
# as much as playing does, while the arm swing of a dig, set or spike has no
# between-rally equivalent.
POSE_ACTIVITY_KEYPOINTS = (L_WRIST, R_WRIST)

# A body centre travelling further than this between two samples is a different
# person, not a sprint: at 3 samples/s it allows 0.75 frame-heights/s of travel,
# which is faster than anyone crosses a court. Same posture as
# MAX_PLAUSIBLE_SPEED_FH — an implausible displacement is a tracking artifact,
# and counting it as motion is what makes dead time look like play.
POSE_MATCH_MAX_SHIFT_FH = 0.25

POSE_MIN_KP_CONF = 0.5   # below this the keypoint is a guess, so it measures nothing

# Rallies are played by a few people while the rest of the court stands and
# watches, so a median over everyone present reads as idle during real play and
# a max reads as noisy. The mean of the top-k is the compromise; k=4 measured
# best on the tuning fixtures, which is about the number of players actually
# engaged in a rally at any instant.
POSE_ACTIVITY_TOP_K = 4

# Wrist travel is divided by the player's own bounding-box height, not the frame
# height: a far-court player's swing covers a fraction of the pixels a near-court
# one's does, and normalizing by the frame would read the far player as idle.
# Body-normalized, both are the same motion. Measured on the tuning fixtures this
# is worth ~0.03 AUC over frame normalization, and it is what makes one threshold
# work across the depth of a court. Activity is therefore in *body*-heights/s,
# unlike every ball speed above.
POSE_NORMALIZE_BY = "body"


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

    Two classes of sample are dropped rather than measured: pairs further apart
    than max_sample_spacing (a tracking dropout, where the displacement says
    nothing about how fast the ball moved) and speeds above max_speed (the
    tracker jumping between two objects). Both would otherwise read as fast
    motion, which is exactly the signal the guarded path trusts.

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
        if v > max_speed:
            continue  # track hop — not a measurement of the ball's motion
        times.append((t0 + t1) / 2)
        vals.append(v)
    return np.array(times), np.array(vals)


def track_is_usable(samples: SpeedSamples, duration: float, *, min_rate: float = 1.0) -> bool:
    """
    Whether the ball track is dense enough for any of this to mean anything.

    Below min_rate usable speed samples per second there is no basis for a
    judgement about play: on fixture test3 the track runs at 0.52/s (vs
    1.47-2.63 on the other four) and 21 of its 32 rallies produce no contact at
    all, so every builder — the rule-based one included — cuts more than half
    its live play. The density also sits under what ANCHOR_MIN_SAMPLES needs, so
    the motion anchor cannot recover those rallies either.
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

    With no speed samples there is nothing to gate on, so every contact stands —
    the caller's abstain guard is what handles a track that thin.
    """
    times, speeds = samples
    if not len(times):
        return list(contacts)

    kept = []
    for c in contacts:
        lo, hi = np.searchsorted(times, [c["time"] - half_window, c["time"] + half_window])
        if hi > lo and float(np.median(speeds[lo:hi])) >= min_speed:
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


# ── pose signal (CF-198) ───────────────────────────────────────────────────
# Everything here produces the same (times, values) shape as speed_samples, so
# the two primitives above — speed_gate_contacts and motion_anchor_windows —
# work on player activity unchanged. They are threshold-and-fraction arithmetic
# over a scalar series and never look at what the scalar measures, so pose gets
# a gate and an anchor without a second implementation of either.


def _person_center(person: dict) -> tuple[float, float]:
    x1, y1, x2, y2 = person["box"]
    return (x1 + x2) / 2, (y1 + y2) / 2


def _keypoint(person: dict, index: int, min_conf: float) -> tuple[float, float] | None:
    """A keypoint's position, or None when the model was not confident in it."""
    conf = person.get("conf")
    if conf is not None and conf[index] < min_conf:
        return None
    x, y = person["kps"][index]
    return float(x), float(y)


def _matched_person_speeds(
    prev_persons: list[dict],
    cur_persons: list[dict],
    frame_height: int,
    dt: float,
    *,
    match_max_shift: float,
    min_kp_conf: float,
    keypoints: tuple[int, ...],
    normalize: str,
) -> list[float]:
    """
    Per-person keypoint speed between two pose samples, in body-heights/s.

    People are matched between samples by nearest bounding-box centre, greedily
    and closest-first, with anything beyond match_max_shift left unmatched. The
    detector emits no identities, and the index order it does emit is not one —
    it reorders between frames, so differencing keypoints by position in the
    list measures people swapping places rather than people moving.

    An unmatched person contributes nothing rather than a fabricated speed, for
    the same reason speed_samples drops a track dropout: the absence of a
    measurement is not a measurement of zero.
    """
    if not prev_persons or not cur_persons:
        return []

    max_shift_px = match_max_shift * frame_height
    prev_centers = [_person_center(p) for p in prev_persons]
    cur_centers = [_person_center(p) for p in cur_persons]

    candidates = sorted(
        (
            (math.hypot(cx - px, cy - py), i, j)
            for i, (cx, cy) in enumerate(cur_centers)
            for j, (px, py) in enumerate(prev_centers)
        ),
        key=lambda c: c[0],
    )

    speeds: list[float] = []
    taken_cur: set[int] = set()
    taken_prev: set[int] = set()
    for dist, i, j in candidates:
        if dist > max_shift_px:
            break   # sorted, so nothing further can match either
        if i in taken_cur or j in taken_prev:
            continue
        taken_cur.add(i)
        taken_prev.add(j)

        if normalize == "body":
            x1, y1, x2, y2 = cur_persons[i]["box"]
            scale = max(y2 - y1, 1.0)
        else:
            scale = frame_height

        displacements = []
        for k in keypoints:
            before = _keypoint(prev_persons[j], k, min_kp_conf)
            after = _keypoint(cur_persons[i], k, min_kp_conf)
            if before is None or after is None:
                continue
            displacements.append(
                math.hypot(after[0] - before[0], after[1] - before[1]) / dt / scale
            )
        if displacements:
            # Max over the two wrists: one arm swings in almost every volleyball
            # action, and averaging it against the idle arm halves the signal.
            speeds.append(max(displacements))
    return speeds


def pose_activity_samples(
    poses: list[dict],
    frame_height: int,
    *,
    max_sample_spacing: float = MAX_SAMPLE_SPACING,
    match_max_shift: float = POSE_MATCH_MAX_SHIFT_FH,
    min_kp_conf: float = POSE_MIN_KP_CONF,
    top_k: int = POSE_ACTIVITY_TOP_K,
    keypoints: tuple[int, ...] = POSE_ACTIVITY_KEYPOINTS,
    normalize: str = POSE_NORMALIZE_BY,
) -> SpeedSamples:
    """
    Per-sample player activity in body-heights/s, shaped like speed_samples.

    The ball signal answers "is the ball moving like it is in play"; this
    answers "is anyone moving like they are playing". Both failure modes CF-187
    left open are invisible to the first question and not to the second: a rally
    the ball detector never sees still has players swinging at something, and a
    contact fired over a stationary spare ball has nobody swinging at all.

    `poses` are the samples from extract_keypoints: {"time", "persons"}, each
    person carrying a pixel-space "box", 17 COCO "kps" and optional "conf".
    Pairs further apart than max_sample_spacing are skipped exactly as in
    speed_samples — a gap in the pose pass says nothing about what happened
    inside it.

    Returns sorted (times, activity); empty arrays when there is nothing usable.
    """
    if frame_height <= 0 or not poses:
        return np.empty(0), np.empty(0)

    ordered = sorted(poses, key=lambda s: s["time"])
    times: list[float] = []
    vals: list[float] = []
    for prev, cur in zip(ordered, ordered[1:]):
        dt = cur["time"] - prev["time"]
        if not 0 < dt <= max_sample_spacing:
            continue
        speeds = _matched_person_speeds(
            prev["persons"], cur["persons"], frame_height, dt,
            match_max_shift=match_max_shift,
            min_kp_conf=min_kp_conf,
            keypoints=keypoints,
            normalize=normalize,
        )
        if not speeds:
            continue
        speeds.sort(reverse=True)
        k = min(top_k, len(speeds))
        times.append((prev["time"] + cur["time"]) / 2)
        vals.append(sum(speeds[:k]) / k)
    return np.array(times), np.array(vals)


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
    pose_activity: SpeedSamples | None = None,
    pose_gate_activity: float | None = None,
    pose_anchor_activity: float | None = None,
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

    Raises on a missing frame height rather than abstaining on one: every speed
    here is normalized by it, so a zero would silently turn every game into an
    abstain. The condense stage catches and falls back to the rule-based windows,
    which need no frame height at all.

    The three pose_* arguments are CF-198's opt-in player-activity signal, and
    each is independent: `pose_gate_activity` additionally requires player
    motion around a contact before it is believed, and `pose_anchor_activity`
    lets sustained player motion open a window of its own. Both default to None,
    which is off — with no `pose_activity` series supplied this function is the
    CF-187 builder exactly, so the setting that switches pose on defaults off and
    the shipping path is unchanged until it is deliberately flipped.
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
        return [(0.0, duration)]

    has_pose = pose_activity is not None and len(pose_activity[0]) > 0

    gated = speed_gate_contacts(contacts, samples, min_speed=gate_speed)
    speed_gated = len(gated)
    if has_pose and pose_gate_activity is not None:
        # Applied after the ball gate, not instead of it: the two reject
        # different false contacts (a stationary ball vs a still court), and a
        # contact needs both kinds of evidence to survive.
        gated = speed_gate_contacts(gated, pose_activity, min_speed=pose_gate_activity)

    windows = active_windows_from_contacts(
        gated, duration,
        gap_seconds=gap_seconds,
        pad_before=pad_before,
        pad_after=pad_after,
        min_contacts=min_contacts,
        merge_gap_seconds=merge_gap_seconds,
    )
    anchors = motion_anchor_windows(samples, duration, speed=anchor_speed, pad=anchor_pad)
    pose_anchors: list[Interval] = []
    if has_pose and pose_anchor_activity is not None:
        pose_anchors = motion_anchor_windows(
            pose_activity, duration, speed=pose_anchor_activity, pad=anchor_pad
        )

    merged = merge_intervals(windows + anchors + pose_anchors, merge_gap_seconds)
    logger.info(
        "Guarded condense windows: %d contacts → %d after speed gate → %d after "
        "pose gate, +%d motion anchors +%d pose anchors → %d windows",
        len(contacts), speed_gated, len(gated), len(anchors), len(pose_anchors), len(merged),
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
