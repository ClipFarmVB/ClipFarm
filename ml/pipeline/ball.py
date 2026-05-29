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

# ── Contact detection config ──────────────────────────────────────────────────
CONTACT_ANGLE_DEG   = 25.0  # minimum direction change (degrees) to flag contact
CONTACT_SPEED_RATIO = 0.35  # OR speed change of this fraction of previous speed
MIN_SPEED_PX        = 4.0   # ignore near-stationary ball (rolling / held)

# ── Rally clipping config ─────────────────────────────────────────────────────
RALLY_GAP_SECONDS    = 5.0   # gap between contacts that splits two rallies
PRE_RALLY_PAD        = 2.0   # seconds before first contact (capture approach)
POST_PLAY_PAD        = 2.5   # seconds after ball leaves play (celebration, flight)
FLOOR_BOUNCE_ANGLE   = 130.0 # direction change >= this = ball hit the floor (unused for splitting, kept for scoring)
FLOOR_BOUNCE_Y_FRAC  = 0.55  # ball must also be in lower N% of frame
MIN_RALLY_DURATION   = 2.0   # skip clips shorter than this (noise/false contacts)
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

    # Stable velocity: average over up to 2 positions either side of contact
    pre  = max(0, i - 2)
    post = min(len(positions) - 1, i + 2)

    a, b = positions[pre], pos
    dt = b.frame - a.frame
    v_before = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    a, b = pos, positions[post]
    dt = b.frame - a.frame
    v_after = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    sp_before = np.hypot(*v_before)
    sp_after  = np.hypot(*v_after)
    vy_before = v_before[1]
    vy_after  = v_after[1]

    # SPIKE: high contact in frame, ball driven hard downward
    if y_frac < 0.45 and vy_after > 2.0 and sp_after > 6.0:
        conf = min(0.88, 0.65 + sp_after / 50.0)
        return "spike", round(conf, 2)

    # BLOCK: high contact, ball reversed from falling to rising (spike blocked back)
    if y_frac < 0.45 and vy_before > 1.0 and vy_after < -1.0:
        return "block", 0.72

    # DIG: low contact, ball was falling, now rising (floor save)
    if y_frac > 0.58 and vy_before > 1.0 and vy_after < -1.0:
        return "dig", 0.75 if sp_after > 3.0 else 0.60

    # SERVE: ball nearly stationary before contact (toss), then driven fast
    if sp_before < 3.0 and sp_after > 6.0:
        return "serve", 0.68

    # SET: controlled mid-height redirect at moderate speed
    if 0.20 < y_frac < 0.65 and 1.5 < sp_after < 8.0:
        return "set", 0.58

    return "unknown", 0.42


def find_contacts(tracker: TrackedBall, frame_height: int = 0) -> list[dict]:
    """
    Scan the tracked trajectory for sharp velocity changes.

    A contact is flagged when:
      - direction changes by >= CONTACT_ANGLE_DEG  (ball deflected)
      - OR speed changes by >= CONTACT_SPEED_RATIO of previous speed (ball accelerated/killed)
      AND the ball is actually moving (speed >= MIN_SPEED_PX)

    Pass frame_height > 0 to get trajectory-based action classification on each contact.

    Returns list of {time, frame, x, y, angle_change, speed_change, action, action_confidence}.
    """
    positions = tracker.positions
    contacts: list[dict] = []

    if len(positions) < 3:
        return contacts

    for i in range(1, len(positions) - 1):
        prev, curr, nxt = positions[i - 1], positions[i], positions[i + 1]

        dt_before = curr.frame - prev.frame
        dt_after  = nxt.frame  - curr.frame
        if dt_before == 0 or dt_after == 0:
            continue

        v_before = ((curr.x - prev.x) / dt_before, (curr.y - prev.y) / dt_before)
        v_after  = ((nxt.x  - curr.x) / dt_after,  (nxt.y  - curr.y) / dt_after)

        speed_before = np.hypot(*v_before)
        speed_after  = np.hypot(*v_after)

        if speed_before < MIN_SPEED_PX and speed_after < MIN_SPEED_PX:
            continue

        angle_change = _angle_between(v_before, v_after)
        speed_change = abs(speed_after - speed_before) / max(speed_before, 1e-6)

        if angle_change >= CONTACT_ANGLE_DEG or speed_change >= CONTACT_SPEED_RATIO:
            action, action_conf = (
                classify_contact_action(positions, i, frame_height)
                if frame_height > 0
                else ("unknown", 0.42)
            )
            contacts.append({
                "time":             curr.time,
                "frame":            curr.frame,
                "x":                curr.x,
                "y":                curr.y,
                "angle_change":     round(angle_change, 1),
                "speed_change":     round(speed_change, 3),
                "action":           action,
                "action_confidence": action_conf,
            })
            logger.debug(
                "Contact at t=%.2fs  angle=%.1f°  speed_Δ=%.2f  action=%s(%.2f)",
                curr.time, angle_change, speed_change, action, action_conf,
            )

    logger.info("Found %d contacts in trajectory of %d positions", len(contacts), len(positions))
    return contacts


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rally clipping
# ─────────────────────────────────────────────────────────────────────────────

def _make_rally(seg: list[dict], video_duration: float) -> dict:
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

    return {
        "start":      max(0.0, seg[0]["time"] - PRE_RALLY_PAD),
        "end":        min(video_duration, seg[-1]["time"] + POST_PLAY_PAD),
        "action":     dominant,
        "confidence": avg_conf,
        "labels":     labels,
    }


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
      3. Each segment becomes one clip:
           rally_start = first_contact.time - PRE_RALLY_PAD  (>= 0)
           rally_end   = last_contact.time  + POST_PLAY_PAD  (<= video_duration)
      4. Clips shorter than MIN_RALLY_DURATION are discarded as noise.

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

    # ── 3 & 4. Build rally windows, discard noise ─────────────────────────────
    rallies: list[dict] = []
    for seg in sorted(final_segments, key=lambda s: s[0]["time"]):
        r = _make_rally(seg, video_duration)
        if r["end"] - r["start"] >= MIN_RALLY_DURATION:
            rallies.append(r)

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
