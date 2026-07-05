"""
Ball detection, tracking, and contact detection pipeline.

Takes a video file and returns a list of contact timestamps — moments
where the ball trajectory changed sharply, indicating a player hit.

Pipeline:
  1. Run Roboflow ball detector on sampled frames  (detect)
  2. Link per-frame detections into a trajectory   (track)
  3. Find sharp velocity changes in the trajectory (contacts)

Usage:
  from ml.pipeline.ball import detect_contacts
  contacts = detect_contacts("game.mp4", api_key="...")
  # -> [{"time": 12.3, "x": 540, "y": 210}, ...]
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── Detection config ──────────────────────────────────────────────────────────
MODEL_ID     = "volleyball-ball-tracking-0eo7r/3"
SAMPLE_EVERY = 3          # run inference every Nth frame (10 fps from 30 fps source)
MIN_CONF     = 0.40       # minimum detection confidence to consider

# ── Tracking config ───────────────────────────────────────────────────────────
MAX_JUMP_PX  = 300        # max pixels a ball can move between sampled frames
                          # detections further than this from the predicted
                          # position are treated as a different object
MAX_MISS     = 5          # max consecutive missed frames before track is reset

# ── Track segmentation config ─────────────────────────────────────────────────
# The raw track is a chimera: _pick_active hops between the game ball, spare
# balls, and false detections (measured: 23% of consecutive positions jump
# >200px). Contacts must only be detected within coherent single-ball
# segments, so the track is split wherever motion is implausible for one ball.
#
# ALL speeds are px/SECOND so thresholds hold at any source frame rate
# (tuned on 30fps footage; a 60fps video halves px/frame velocities but
# leaves px/second untouched).
SEG_MAX_SPEED_PXPS        = 1200.0  # px/s: faster displacement = track hop, split here
SEG_MIN_POSITIONS         = 4       # segments shorter than this are junk (hops/flicker)
SEG_MIN_MEDIAN_SPEED_PXPS = 60.0    # px/s: near-stationary segments are held/spare balls

# ── Contact detection config ──────────────────────────────────────────────────
# A contact is a deviation from ballistic (free-fall) flight. Raw angle/speed
# thresholds misfire badly at coarse sampling: gravity alone bends the
# trajectory >25° between samples 0.33s apart, flagging normal flight as hits.
# Gravity is estimated per-video from coherent segments (median vertical
# acceleration — free flight dominates, so the median is robust to hits).
# Thresholds tuned against a real cached trajectory: detector centroid wobble
# puts the residual noise floor at ~p75 = 450 px/s (15 px/frame @ 30fps).
CONTACT_RESIDUAL_RATIO    = 0.50    # residual must exceed this fraction of ball speed
CONTACT_RESIDUAL_MIN_PXPS = 480.0   # ...and this absolute floor (px/s, above noise)
CONTACT_HIT_SPEED_PXPS    = 240.0   # px/s: a real hit has speed on at least one side
MIN_CONTACT_SPACING       = 0.6     # seconds: debounce — one hit can't fire twice
MAX_SAMPLE_GAP_SEC        = 1.0     # skip triples spanning a detection gap
MIN_SPEED_PXPS            = 120.0   # px/s: ignore near-stationary ball (rolling / held)
# Legacy thresholds — still used by fuse_with_ball_contacts "strong contact" check
CONTACT_ANGLE_DEG   = 25.0  # minimum direction change (degrees)
CONTACT_SPEED_RATIO = 0.35  # speed change fraction

# ── Rally clipping config ─────────────────────────────────────────────────────
RALLY_GAP_SECONDS    = 5.0   # gap between contacts that splits two rallies
# NOTE (CF-38 review): labeled data shows ~5 clips/game cut off just before the
# point-ending hit (fastest ball escapes the tracker, so the window anchors to
# the previous contact). Larger pads fix that but add slack to every clip while
# dead-time contacts still inflate windows — revisit after CF-22/CF-5 remove
# dead time, when bigger pads become nearly free.
PRE_RALLY_PAD        = 2.0   # seconds before first contact (capture approach)
POST_PLAY_PAD        = 2.5   # seconds after ball leaves play (celebration, flight)
# Rally contacts arrive every 1-2s; ball-shagging tosses and pre-serve bounces
# trail/lead sparsely. An edge contact separated from the rally body by more
# than this is dead time, not play (labeled cost: 5-10s dead tails/leads).
EDGE_CONTACT_GAP     = 3.0   # seconds: trim rally-edge contacts beyond this gap
# When a long rally is split on an internal contact gap, the gap often IS the
# highlight (fast spike = tracker miss). Bridge small windows between adjacent
# clips so that moment lands in at least one of them.
MAX_HOLE_TO_BRIDGE   = 4.0   # seconds: close gaps between consecutive clip windows
HOLE_OVERLAP         = 0.75  # seconds each side reaches past the hole midpoint
FLOOR_BOUNCE_ANGLE   = 130.0 # direction change >= this = ball hit the floor (unused for splitting, kept for scoring)
FLOOR_BOUNCE_Y_FRAC  = 0.55  # ball must also be in lower N% of frame
MIN_RALLY_DURATION   = 2.0   # skip clips shorter than this (noise/false contacts)
MIN_RALLY_CONTACTS   = 3     # a rally needs a serve + return at minimum; 1-2 = noise
MAX_CLIP_DURATION    = 30.0  # split long groups into sub-clips of at most this length


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BallPosition:
    frame: int
    time: float
    x: float
    y: float
    confidence: float


@dataclass
class TrackedBall:
    """Running state of the tracked active ball."""
    positions: list[BallPosition] = field(default_factory=list)
    misses: int = 0

    @property
    def last(self) -> Optional[BallPosition]:
        return self.positions[-1] if self.positions else None

    @property
    def velocity(self) -> Optional[tuple[float, float]]:
        """Velocity vector (vx, vy) in px/frame from the last two positions."""
        if len(self.positions) < 2:
            return None
        a, b = self.positions[-2], self.positions[-1]
        dt = b.frame - a.frame
        if dt == 0:
            return None
        return (b.x - a.x) / dt, (b.y - a.y) / dt

    def predict_next(self, at_frame: int) -> Optional[tuple[float, float]]:
        """Extrapolate ball position at `at_frame` using current velocity."""
        if self.last is None:
            return None
        v = self.velocity
        if v is None:
            return self.last.x, self.last.y
        dt = at_frame - self.last.frame
        return self.last.x + v[0] * dt, self.last.y + v[1] * dt


# ─────────────────────────────────────────────────────────────────────────────
# 1. Detection
# ─────────────────────────────────────────────────────────────────────────────

def _load_model(api_key: str):
    """Load Roboflow ball detection model (weights cached after first run)."""
    from inference import get_model
    logger.info("Loading ball detection model %s", MODEL_ID)
    return get_model(MODEL_ID, api_key=api_key)


def _detect_frame(model, frame: np.ndarray) -> list[dict]:
    """
    Run inference on a single frame.
    Returns list of {x, y, confidence} dicts, sorted by confidence desc.
    """
    results = model.infer(frame, confidence=MIN_CONF)
    preds = []
    if results and hasattr(results[0], "predictions"):
        for p in results[0].predictions:
            preds.append({"x": float(p.x), "y": float(p.y), "confidence": float(p.confidence)})
    preds.sort(key=lambda d: d["confidence"], reverse=True)
    return preds


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tracking
# ─────────────────────────────────────────────────────────────────────────────

def _pick_active(
    detections: list[dict],
    tracker: TrackedBall,
    frame: int,
    max_jump: float = MAX_JUMP_PX,
    max_age_frames: int = 0,
) -> Optional[dict]:
    """
    Given multiple detections in a frame, return the one most likely to be
    the active ball by proximity to the predicted trajectory position.

    If the track is stale (last detection older than max_age_frames) the
    prediction is discarded and the highest-confidence detection is accepted
    directly — this prevents stale extrapolation from poisoning new tracks.
    """
    if not detections:
        return None

    # Check if the existing track is too old to trust
    track_stale = (
        max_age_frames > 0
        and tracker.last is not None
        and (frame - tracker.last.frame) > max_age_frames
    )

    predicted = None if track_stale else tracker.predict_next(frame)

    if predicted is None:
        # No track or stale track — accept the highest-confidence detection
        return detections[0]

    px, py = predicted
    best = None
    best_dist = float("inf")
    for d in detections:
        dist = np.hypot(d["x"] - px, d["y"] - py)
        if dist < best_dist:
            best_dist = dist
            best = d

    if best_dist > max_jump:
        # Too far from predicted position — treat as new track segment
        return detections[0]
    return best


def track_ball(video_path: str, api_key: str, sample_every: int = SAMPLE_EVERY) -> TrackedBall:
    """
    Run detection on every sample_every frame and build a trajectory for
    the active ball, ignoring stationary spare balls.

    Returns a TrackedBall with all confirmed positions.
    """
    model = _load_model(api_key)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info("Tracking ball: %d frames @ %.1f fps (sample_every=%d)", total_frames, fps, sample_every)

    # Scale the jump threshold by the sampling interval so fast-moving balls
    # aren't rejected when sample_every is large (e.g. 10 vs the default 3).
    max_jump      = MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)
    # After this many frames without a detection, treat the track as lost:
    # predict_next returns None and the next detection starts a fresh segment.
    max_age_frames = MAX_MISS * sample_every

    tracker   = TrackedBall()
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % sample_every == 0:
            detections = _detect_frame(model, frame)
            active = _pick_active(detections, tracker, frame_idx,
                                  max_jump=max_jump, max_age_frames=max_age_frames)

            if active:
                tracker.misses = 0
                tracker.positions.append(BallPosition(
                    frame=frame_idx,
                    time=frame_idx / fps,
                    x=active["x"],
                    y=active["y"],
                    confidence=active["confidence"],
                ))
            else:
                tracker.misses += 1

        frame_idx += 1

    cap.release()
    logger.info("Tracked %d ball positions", len(tracker.positions))
    return tracker


# ─────────────────────────────────────────────────────────────────────────────
# 3. Contact detection
# ─────────────────────────────────────────────────────────────────────────────

def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """Angle in degrees between two 2-D vectors."""
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    cos_theta = np.dot(v1, v2) / (n1 * n2)
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_theta)))


def classify_contact_action(
    positions: list[BallPosition],
    i: int,
    frame_height: int,
) -> tuple[str, float]:
    """
    Classify a volleyball action from ball trajectory at contact index i.

    Uses the velocity vectors immediately before and after the contact point,
    plus the ball's height in the frame, to determine what kind of hit occurred.

    In image coordinates Y increases downward:
      vy > 0  = ball falling
      vy < 0  = ball rising

    Returns (action_type, confidence).
    """
    pos    = positions[i]
    y_frac = pos.y / max(frame_height, 1)   # 0 = top of frame, 1 = bottom

    # Stable velocity (px/s): average over up to 2 positions either side
    pre  = max(0, i - 2)
    post = min(len(positions) - 1, i + 2)

    a, b = positions[pre], pos
    dt = b.time - a.time
    v_before = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    a, b = pos, positions[post]
    dt = b.time - a.time
    v_after = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    sp_before = np.hypot(*v_before)
    sp_after  = np.hypot(*v_after)
    vy_before = v_before[1]
    vy_after  = v_after[1]

    # Thresholds in px/s (tuned values × 30 from the original 30fps px/frame)
    # SPIKE: high contact in frame, ball driven hard downward
    if y_frac < 0.45 and vy_after > 60.0 and sp_after > 180.0:
        conf = min(0.88, 0.65 + sp_after / 1500.0)
        return "spike", round(conf, 2)

    # BLOCK: high contact, ball reversed from falling to rising (spike blocked back)
    if y_frac < 0.45 and vy_before > 30.0 and vy_after < -30.0:
        return "block", 0.72

    # DIG: low contact, ball was falling, now rising (floor save)
    if y_frac > 0.58 and vy_before > 30.0 and vy_after < -30.0:
        return "dig", 0.75 if sp_after > 90.0 else 0.60

    # SERVE: ball nearly stationary before contact (toss), then driven fast
    if sp_before < 90.0 and sp_after > 180.0:
        return "serve", 0.68

    # SET: controlled mid-height redirect at moderate speed
    if 0.20 < y_frac < 0.65 and 45.0 < sp_after < 240.0:
        return "set", 0.58

    return "unknown", 0.42


def _segment_track(positions: list[BallPosition]) -> list[list[BallPosition]]:
    """
    Split the raw track into coherent single-ball segments.

    The tracker hops between the game ball, spare balls, and false positives,
    so the positions list is a chimera of multiple objects. Split wherever the
    implied speed exceeds SEG_MAX_SPEED_PXPF (teleport = different object) or
    the detection gap exceeds MAX_SEGMENT_GAP_FRAMES, then drop segments that
    are too short (flicker) or near-stationary (held/spare balls).
    """
    if not positions:
        return []

    raw: list[list[BallPosition]] = []
    cur = [positions[0]]
    for a, b in zip(positions, positions[1:]):
        dt = b.time - a.time
        if dt <= 0:
            continue
        speed = np.hypot(b.x - a.x, b.y - a.y) / dt  # px/s
        if dt > MAX_SAMPLE_GAP_SEC or speed > SEG_MAX_SPEED_PXPS:
            raw.append(cur)
            cur = [b]
        else:
            cur.append(b)
    raw.append(cur)

    kept = []
    for seg in raw:
        if len(seg) < SEG_MIN_POSITIONS:
            continue
        speeds = [
            np.hypot(b.x - a.x, b.y - a.y) / max(b.time - a.time, 1e-6)
            for a, b in zip(seg, seg[1:])
        ]
        if np.median(speeds) < SEG_MIN_MEDIAN_SPEED_PXPS:
            continue
        kept.append(seg)

    logger.info(
        "Track segmentation: %d positions → %d segments, %d kept (%d positions)",
        len(positions), len(raw), len(kept), sum(len(s) for s in kept),
    )
    return kept


def _estimate_gravity(segments: list[list[BallPosition]]) -> float:
    """
    Estimate gravity in px/s² from coherent flight segments.

    Median vertical acceleration across all within-segment triples. Free
    flight dominates (contacts are rare), so the median is robust regardless
    of camera distance/zoom. Clamped to >= 0 (image Y grows downward).
    """
    accels = []
    for seg in segments:
        for i in range(1, len(seg) - 1):
            dt_b = seg[i].time - seg[i - 1].time
            dt_a = seg[i + 1].time - seg[i].time
            if dt_b <= 0 or dt_a <= 0:
                continue
            vy_b = (seg[i].y - seg[i - 1].y) / dt_b
            vy_a = (seg[i + 1].y - seg[i].y) / dt_a
            accels.append((vy_a - vy_b) / ((dt_b + dt_a) / 2))
    if not accels:
        return 0.0
    return max(float(np.median(accels)), 0.0)


def find_contacts(tracker: TrackedBall, frame_height: int = 0) -> list[dict]:
    """
    Find player contacts: deviations from ballistic flight within coherent
    track segments.

    Pipeline: segment the raw track (drop hops/junk) → estimate gravity from
    the segments → within each segment, flag samples whose post-velocity
    deviates from the ballistic prediction by more than the noise floor
    (CONTACT_RESIDUAL_MIN_PXPS, or CONTACT_RESIDUAL_RATIO × speed if higher)
    with plausible hit speed on at least one side → global time-sorted
    debounce (MIN_CONTACT_SPACING). All velocities are px/s, so behaviour is
    identical across source frame rates.

    Thresholds are tuned against real footage; see constants at module top.

    Pass frame_height > 0 to get trajectory-based action classification.

    Returns list of {time, frame, x, y, angle_change, speed_change,
    speed_before, speed_after, residual, action, action_confidence}.
    """
    segments = _segment_track(tracker.positions)
    if not segments:
        return []

    g_px = _estimate_gravity(segments)
    logger.info("Estimated gravity: %.3f px/s²", g_px)

    candidates: list[dict] = []

    for seg in segments:
        for i in range(1, len(seg) - 1):
            prev, curr, nxt = seg[i - 1], seg[i], seg[i + 1]

            dt_before = curr.time - prev.time
            dt_after  = nxt.time  - curr.time
            if dt_before <= 0 or dt_after <= 0:
                continue

            v_before = ((curr.x - prev.x) / dt_before, (curr.y - prev.y) / dt_before)
            v_after  = ((nxt.x  - curr.x) / dt_after,  (nxt.y  - curr.y) / dt_after)

            speed_before = np.hypot(*v_before)
            speed_after  = np.hypot(*v_after)

            if speed_before < MIN_SPEED_PXPS and speed_after < MIN_SPEED_PXPS:
                continue
            # A real hit imparts speed — both sides slow = detector wobble
            if max(speed_before, speed_after) < CONTACT_HIT_SPEED_PXPS:
                continue

            # Ballistic prediction: horizontal velocity persists, vertical gains gravity
            dt_mid = (dt_before + dt_after) / 2
            predicted_vy = v_before[1] + g_px * dt_mid
            residual = float(np.hypot(v_after[0] - v_before[0], v_after[1] - predicted_vy))

            threshold = max(CONTACT_RESIDUAL_MIN_PXPS, CONTACT_RESIDUAL_RATIO * speed_before)
            if residual < threshold:
                continue

            angle_change = _angle_between(v_before, v_after)
            speed_change = abs(speed_after - speed_before) / max(speed_before, 1e-6)

            action, action_conf = (
                classify_contact_action(seg, i, frame_height)
                if frame_height > 0
                else ("unknown", 0.42)
            )
            candidates.append({
                "time":             curr.time,
                "frame":            curr.frame,
                "x":                curr.x,
                "y":                curr.y,
                "angle_change":     round(angle_change, 1),
                "speed_change":     round(speed_change, 3),
                "speed_before":     round(float(speed_before), 2),
                "speed_after":      round(float(speed_after), 2),
                "residual":         round(residual, 2),
                "action":           action,
                "action_confidence": action_conf,
            })

    # Global time-sorted debounce: one hit disturbs adjacent samples too
    candidates.sort(key=lambda c: c["time"])
    contacts: list[dict] = []
    last_contact_time = float("-inf")
    for c in candidates:
        if c["time"] - last_contact_time < MIN_CONTACT_SPACING:
            continue
        last_contact_time = c["time"]
        contacts.append(c)

    logger.info(
        "Found %d contacts in trajectory of %d positions",
        len(contacts), len(tracker.positions),
    )
    return contacts


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rally clipping
# ─────────────────────────────────────────────────────────────────────────────

def _make_rally(seg: list[dict], video_duration: float, frame_height: int = 0) -> dict:
    """Build a rally clip dict from a list of contacts."""
    action_scores: dict[str, float] = {}
    action_counts: dict[str, int] = {}
    for c in seg:
        a = c.get("action", "unknown")
        if a != "unknown":
            action_scores[a] = action_scores.get(a, 0.0) + c.get("action_confidence", 0.0)
            action_counts[a] = action_counts.get(a, 0) + 1

    if action_scores:
        dominant = max(action_scores, key=action_scores.__getitem__)
        # Divide by contacts that contributed to dominant action (not total contacts)
        avg_conf = round(action_scores[dominant] / max(action_counts[dominant], 1), 3)
        seen: dict[str, None] = {}
        for c in seg:
            a = c.get("action", "unknown")
            if a != "unknown":
                seen[a] = None
        labels = list(seen.keys())
    else:
        dominant, avg_conf, labels = "unknown", 0.50, []

    first_contact = seg[0]["time"]
    last_contact  = seg[-1]["time"]

    # Rally features for downstream highlight scoring (ml/pipeline/score.py).
    # Speeds are normalized to frame-heights per SECOND so they are comparable
    # across both resolutions and frame rates.
    speed_div = float(frame_height) if frame_height > 0 else 1.0
    max_speed = max(
        (max(c.get("speed_before", 0.0), c.get("speed_after", 0.0)) for c in seg),
        default=0.0,
    ) / speed_div
    sharp_changes = sum(1 for c in seg if c.get("angle_change", 0.0) >= 60.0)
    floor_bounce = any(
        c.get("angle_change", 0.0) >= FLOOR_BOUNCE_ANGLE
        and frame_height > 0
        and c.get("y", 0.0) / frame_height >= FLOOR_BOUNCE_Y_FRAC
        for c in seg
    )
    contact_span = max(last_contact - first_contact, 1e-6)

    return {
        "start":      max(0.0, first_contact - PRE_RALLY_PAD),
        "end":        min(video_duration, last_contact + POST_PLAY_PAD),
        "action":     dominant,
        "confidence": avg_conf,
        "labels":     labels,
        "features": {
            "contact_count":  len(seg),
            "first_contact":  round(first_contact, 2),
            "last_contact":   round(last_contact, 2),
            "duration":       round(contact_span, 2),
            "max_speed":      round(max_speed, 4),
            "sharp_changes":  sharp_changes,
            "floor_bounce":   floor_bounce,
            "contact_rate":   round(len(seg) / contact_span, 3),
        },
    }


def _trim_edge_contacts(seg: list[dict]) -> list[dict]:
    """
    Drop sparse leading/trailing contacts from a rally group.

    Rally play produces contacts every 1-2s; pre-serve ball bounces and
    post-point shagging tosses attach to the group's edges with larger gaps.
    Peel contacts off both ends while the edge gap exceeds EDGE_CONTACT_GAP,
    keeping at least MIN_RALLY_CONTACTS. (CF-38: labeled clips carried 5-10s
    of dead time caused by exactly these edge contacts.)
    """
    seg = list(seg)
    while len(seg) > MIN_RALLY_CONTACTS and seg[1]["time"] - seg[0]["time"] > EDGE_CONTACT_GAP:
        seg.pop(0)
    while len(seg) > MIN_RALLY_CONTACTS and seg[-1]["time"] - seg[-2]["time"] > EDGE_CONTACT_GAP:
        seg.pop()
    return seg


def _bridge_small_holes(rallies: list[dict], video_duration: float) -> list[dict]:
    """
    Close small windows between consecutive clips so nothing falls in the crack.

    When a long rally is subdivided on its largest internal contact gap, that
    gap is frequently the point-ending spike (fastest ball = detector miss) —
    labeled example: a spike that appeared in neither of the two adjacent
    clips. Extend both windows past the hole midpoint so the moment lands in
    each. Holes wider than MAX_HOLE_TO_BRIDGE are genuine between-rally time.
    """
    for a, b in zip(rallies, rallies[1:]):
        hole = b["start"] - a["end"]
        if 0 < hole <= MAX_HOLE_TO_BRIDGE:
            mid = (a["end"] + b["start"]) / 2
            a["end"]   = min(video_duration, mid + HOLE_OVERLAP)
            b["start"] = max(0.0, mid - HOLE_OVERLAP)
    return rallies


def contacts_to_rallies(
    contacts: list[dict],
    video_duration: float,
    frame_height: int,
) -> list[dict]:
    """
    Convert a contact list into rally clip boundaries.

    Algorithm:
      1. Group contacts by time gap: a new segment starts when the gap to the
         previous contact exceeds RALLY_GAP_SECONDS.
      2. Segments longer than MAX_CLIP_DURATION are subdivided on their largest
         internal gaps so each sub-clip stays under the cap.
      3. Sparse edge contacts (pre-serve bounces, shag tosses) are trimmed so
         windows hug actual play (_trim_edge_contacts).
      4. Each segment becomes one clip:
           rally_start = first_contact.time - PRE_RALLY_PAD  (>= 0)
           rally_end   = last_contact.time  + POST_PLAY_PAD  (<= video_duration)
      5. Clips shorter than MIN_RALLY_DURATION are discarded as noise.
      6. Small holes between consecutive windows are bridged so split-point
         moments appear in at least one clip (_bridge_small_holes).

    Returns list of dicts compatible with generate_clips():
      {start, end, action, confidence, labels}
    """
    if not contacts:
        return []

    sorted_contacts = sorted(contacts, key=lambda c: c["time"])

    # ── 1. Group by large time gaps ───────────────────────────────────────────
    groups: list[list[dict]] = []
    current: list[dict] = [sorted_contacts[0]]
    for c in sorted_contacts[1:]:
        if c["time"] - current[-1]["time"] > RALLY_GAP_SECONDS:
            groups.append(current)
            current = [c]
        else:
            current.append(c)
    groups.append(current)

    # ── 2. Sub-divide groups that are too long ────────────────────────────────
    final_segments: list[list[dict]] = []
    for grp in groups:
        span = grp[-1]["time"] - grp[0]["time"]
        if span <= MAX_CLIP_DURATION or len(grp) < 4:
            final_segments.append(grp)
            continue

        # Repeatedly split on the largest internal gap until all sub-groups fit
        pending = [grp]
        while pending:
            seg = pending.pop()
            span = seg[-1]["time"] - seg[0]["time"]
            if span <= MAX_CLIP_DURATION or len(seg) < 4:
                final_segments.append(seg)
                continue
            # Find largest gap between consecutive contacts in this segment
            max_gap = 0.0
            split_idx = 1
            for i in range(1, len(seg)):
                g = seg[i]["time"] - seg[i - 1]["time"]
                if g > max_gap:
                    max_gap = g
                    split_idx = i
            pending.append(seg[:split_idx])
            pending.append(seg[split_idx:])

    # ── 3-5. Trim edges, build rally windows, discard noise ──────────────────
    rallies: list[dict] = []
    for seg in sorted(final_segments, key=lambda s: s[0]["time"]):
        seg = _trim_edge_contacts(seg)
        if len(seg) < MIN_RALLY_CONTACTS:
            continue  # 1-2 isolated contacts = noise, not a rally
        r = _make_rally(seg, video_duration, frame_height)
        if r["end"] - r["start"] >= MIN_RALLY_DURATION:
            rallies.append(r)

    # ── 6. Bridge small holes so split-point moments aren't lost ─────────────
    rallies = _bridge_small_holes(rallies, video_duration)

    logger.info(
        "contacts_to_rallies: %d contacts -> %d rallies",
        len(contacts), len(rallies),
    )
    return rallies


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def detect_contacts(
    video_path: str,
    api_key: str | None = None,
    sample_every: int = SAMPLE_EVERY,
) -> list[dict]:
    """
    Full pipeline: detect ball -> track trajectory -> find contacts.

    Returns list of contact dicts with keys:
      time, frame, x, y, angle_change, speed_change

    api_key defaults to the ROBOFLOW_API_KEY environment variable.
    sample_every: run inference every N frames (higher = faster, less precise).
    """
    key = api_key or os.environ.get("ROBOFLOW_API_KEY", "")
    if not key:
        raise ValueError("ROBOFLOW_API_KEY not set and api_key not provided")

    tracker  = track_ball(video_path, key, sample_every=sample_every)
    contacts = find_contacts(tracker)
    return contacts


def detect_rallies(
    video_path: str,
    api_key: str | None = None,
    sample_every: int = SAMPLE_EVERY,
) -> list[dict]:
    """
    Full pipeline: detect ball -> track -> contacts -> rally clip boundaries.

    Returns list of rally dicts ready for generate_clips():
      {start, end, action, confidence, labels}
    """
    key = api_key or os.environ.get("ROBOFLOW_API_KEY", "")
    if not key:
        raise ValueError("ROBOFLOW_API_KEY not set and api_key not provided")

    cap = cv2.VideoCapture(video_path)
    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    video_duration = total_frames / fps

    tracker  = track_ball(video_path, key, sample_every=sample_every)
    contacts = find_contacts(tracker)
    rallies  = contacts_to_rallies(contacts, video_duration, frame_height)
    return rallies
