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
RALLY_GAP_SECONDS    = 8.0   # gap between contacts that splits two rallies
PRE_RALLY_PAD        = 2.0   # seconds before first contact (capture approach)
POST_PLAY_PAD        = 2.5   # seconds after ball leaves play (celebration, flight)
FLOOR_BOUNCE_ANGLE   = 130.0 # direction change >= this = ball hit the floor
FLOOR_BOUNCE_Y_FRAC  = 0.55  # ball must also be in lower N% of frame


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
) -> Optional[dict]:
    """
    Given multiple detections in a frame, return the one most likely to be
    the active ball by proximity to the predicted trajectory position.
    Falls back to highest-confidence detection if no track exists yet.
    """
    if not detections:
        return None

    predicted = tracker.predict_next(frame)
    if predicted is None:
        # No track yet — start with the highest-confidence detection
        return detections[0]

    px, py = predicted
    best = None
    best_dist = float("inf")
    for d in detections:
        dist = np.hypot(d["x"] - px, d["y"] - py)
        if dist < best_dist:
            best_dist = dist
            best = d

    # Reject if even the closest detection is too far (likely a different ball)
    if best_dist > MAX_JUMP_PX:
        return None
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

    tracker   = TrackedBall()
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % sample_every == 0:
            detections = _detect_frame(model, frame)
            active = _pick_active(detections, tracker, frame_idx)

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
                if tracker.misses >= MAX_MISS:
                    # Track lost for too long — reset so next detection
                    # starts fresh rather than snapping across the frame
                    logger.debug("Track reset at frame %d (too many misses)", frame_idx)
                    tracker.misses = 0

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


def find_contacts(tracker: TrackedBall) -> list[dict]:
    """
    Scan the tracked trajectory for sharp velocity changes.

    A contact is flagged when:
      - direction changes by >= CONTACT_ANGLE_DEG  (ball deflected)
      - OR speed changes by >= CONTACT_SPEED_RATIO of previous speed (ball accelerated/killed)
      AND the ball is actually moving (speed >= MIN_SPEED_PX)

    Returns list of {time, frame, x, y, angle_change, speed_change} dicts.
    """
    positions = tracker.positions
    contacts  = []

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

        # Skip near-stationary ball (rolling, held, bouncing slowly on floor)
        if speed_before < MIN_SPEED_PX and speed_after < MIN_SPEED_PX:
            continue

        angle_change = _angle_between(v_before, v_after)
        speed_change = abs(speed_after - speed_before) / max(speed_before, 1e-6)

        if angle_change >= CONTACT_ANGLE_DEG or speed_change >= CONTACT_SPEED_RATIO:
            contacts.append({
                "time":         curr.time,
                "frame":        curr.frame,
                "x":            curr.x,
                "y":            curr.y,
                "angle_change": round(angle_change, 1),
                "speed_change": round(speed_change, 3),
            })
            logger.debug(
                "Contact at t=%.2fs  angle=%.1f deg  speed_change=%.2f",
                curr.time, angle_change, speed_change,
            )

    logger.info("Found %d contacts in trajectory of %d positions", len(contacts), len(positions))
    return contacts


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rally clipping
# ─────────────────────────────────────────────────────────────────────────────

def contacts_to_rallies(
    contacts: list[dict],
    video_duration: float,
    frame_height: int,
) -> list[dict]:
    """
    Convert a contact list into rally clip boundaries.

    Algorithm:
      1. Group contacts separated by <= RALLY_GAP_SECONDS into one rally.
      2. Within each rally find the termination contact — the first contact
         where the ball hits the floor (high angle reversal in lower frame).
         If none is found the last contact is used as the termination.
      3. rally_start = first_contact.time - PRE_RALLY_PAD  (>= 0)
         rally_end   = termination.time   + POST_PLAY_PAD  (<= video_duration)

    Returns list of dicts compatible with generate_clips():
      {start, end, action, confidence, labels}
    """
    if not contacts:
        return []

    # ── 1. Group contacts into rallies ────────────────────────────────────────
    sorted_contacts = sorted(contacts, key=lambda c: c["time"])

    groups: list[list[dict]] = []
    current: list[dict] = [sorted_contacts[0]]
    for c in sorted_contacts[1:]:
        if c["time"] - current[-1]["time"] <= RALLY_GAP_SECONDS:
            current.append(c)
        else:
            groups.append(current)
            current = [c]
    groups.append(current)

    # ── 2 & 3. Find termination contact and build clip boundaries ─────────────
    floor_y_threshold = frame_height * FLOOR_BOUNCE_Y_FRAC
    rallies: list[dict] = []

    for group in groups:
        first = group[0]
        rally_start = max(0.0, first["time"] - PRE_RALLY_PAD)

        # Find the first floor bounce in this rally
        termination = None
        for c in group:
            is_floor_angle = c["angle_change"] >= FLOOR_BOUNCE_ANGLE
            is_low_in_frame = c["y"] >= floor_y_threshold
            if is_floor_angle and is_low_in_frame:
                termination = c
                break

        # Fallback: use the last contact if no floor bounce found
        if termination is None:
            termination = group[-1]

        rally_end = min(video_duration, termination["time"] + POST_PLAY_PAD)

        # Skip degenerate clips (can happen if contacts are at the very end)
        if rally_end <= rally_start:
            continue

        rallies.append({
            "start":      rally_start,
            "end":        rally_end,
            "action":     "unknown",   # pose classifier can label later
            "confidence": 1.0,
            "labels":     [],
        })

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
