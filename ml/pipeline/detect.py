"""
Action detection pipeline.

Phase 1 (MVP): Rule-based heuristics on YOLOv8-pose skeleton keypoints.
Phase 2 (v1.1): Replace/augment with trained MLP/LSTM classifier.

Outputs a list of detections:
  [{"start": float, "end": float, "action": str, "confidence": float}, ...]
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _model_path(name: str) -> str:
    """
    Resolve a bare YOLO weight filename against MODELS_DIR when set.

    ultralytics downloads named weights to the given path; without a stable
    directory that means a fresh download into the (ephemeral) working dir on
    every container recreate. MODELS_DIR points at a persistent volume so the
    download happens once (CF-40). Absolute/relative paths pass through.
    """
    models_dir = os.environ.get("MODELS_DIR", "")
    if models_dir and os.sep not in name and "/" not in name:
        return os.path.join(models_dir, name)
    return name

class PoseStreamError(RuntimeError):
    """The video stream died partway through refinement.

    Distinct from "this window had nothing to see": it means the decoder stopped
    returning frames after having returned them, so every remaining window would
    silently keep its trajectory label while the run still reported success.
    Reachable since refinement reads the presigned URL in place rather than a
    fully-downloaded local file — a mid-run network fault now looks exactly like
    end-of-video to `cap.read()`.
    """


class PoseRuntimeUnavailable(RuntimeError):
    """No usable pose runtime, and fabricated detections were not permitted.

    Subclasses RuntimeError so existing handling still catches it; the distinct
    type lets callers recognise it as PERMANENT — no amount of retrying makes
    ultralytics appear — and skip an expensive retry loop.
    """


def pose_available() -> bool:
    """Whether pose inference can actually run in this process.

    Keyed on the import succeeding rather than on the package being installed:
    a broken torch fails `from ultralytics import YOLO` with ultralytics present,
    which a `find_spec` check would call available.
    """
    try:
        from ultralytics import YOLO  # noqa: F401
    except ImportError:
        return False
    return True


ActionType = Literal["spike", "serve", "dig", "set", "block", "unknown"]

# YOLOv8 pose keypoint indices (COCO 17-point)
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# ── Ball fusion config ────────────────────────────────────────────────────────
BALL_POSE_WINDOW   = 2.5   # seconds: max gap between ball contact time and pose peak to link
BALL_STRONG_ANGLE  = 60.0  # degrees: unmatched contacts above this create a clip anyway
BALL_STRONG_SPEED  = 0.50  # speed ratio: unmatched contacts above this create a clip anyway
BALL_ONLY_CONF     = 0.62  # confidence for ball-confirmed but pose-unlabeled clips
NO_BALL_PENALTY    = 0.45  # factor applied to pose confidence when no ball contact found nearby

# ── Sliding window config ─────────────────────────────────────────────────────
WINDOW_FRAMES = 30   # 1 s @ 30 fps
STRIDE_FRAMES = 10   # 10-frame stride (3× overlap)
SKIP_FRAMES = 4      # Analyse every Nth frame for speed (4× speedup)

# Clip padding around detected peak
PAD_BEFORE = 2.0   # seconds
PAD_AFTER = 3.0    # seconds
MIN_CLIP_GAP = 1.5  # Merge detections closer than this (seconds)

# ── Rally grouping config ─────────────────────────────────────────────────────
RALLY_GAP_SECONDS = 8.0  # Max gap between consecutive clips to consider same rally

# ── Quality filters ──────────────────────────────────────────────────────────
MIN_GROUP_FRAMES = 3       # Minimum frame-level detections to form a clip
MIN_CONFIDENCE = 0.50      # Discard detections below this confidence
SPECTATOR_ZONE = 0.20      # Ignore poses in the top 20% of the frame (stands/crowd)
MOTION_THRESHOLD = 0.015   # Minimum wrist velocity (fraction of frame height per frame)


@dataclass
class Detection:
    peak_time: float
    action: ActionType
    confidence: float
    start: float = field(init=False)
    end: float = field(init=False)

    def __post_init__(self):
        self.start = max(0.0, self.peak_time - PAD_BEFORE)
        self.end = self.peak_time + PAD_AFTER


def run_detection(
    video_path: str,
    model_name: str = "yolov8s-pose.pt",  # small model — detects far-side players at imgsz=1280
    imgsz: int = 1280,
    skip_frames: int = SKIP_FRAMES,
    allow_stub: bool = False,
) -> list[dict]:
    """
    Run full detection pipeline on a video file.
    Returns list of {start, end, action, confidence} dicts.

    Takes the same three quality knobs as `classify_within_windows` and for the
    same reason: both are driven by the POSE_* settings, and a fallback scan
    that ignored them would silently run a different (heavier) config than the
    refinement path on the very same deployment.

    `allow_stub` gates `_stub_detections` — fabricated clips at invented
    timestamps, a development affordance for environments without a pose
    runtime. It defaults to OFF because callers persist what this returns: a
    caller that wants fake clips has to say so. The guard lives here rather than
    in the caller because the trigger is this import failing, which is not the
    same question as whether ultralytics is installed (a broken torch fails the
    import with ultralytics present).
    """
    try:
        from ultralytics import YOLO
        model = YOLO(_model_path(model_name))
    except ImportError as import_err:
        if not allow_stub:
            raise PoseRuntimeUnavailable(
                f"Pose detection is unavailable ({import_err}) and stub detections are "
                "not allowed. Refusing to return fabricated clips — pass allow_stub=True "
                "only where invented data is acceptable (local development)."
            ) from import_err
        logger.warning("ultralytics not installed — returning stub detections")
        return _stub_detections(video_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    logger.info("Video: %.1f fps, %d frames (%.0f s), %dx%dpx", fps, total_frames, total_frames / fps, frame_w, frame_h)

    motion_px      = MOTION_THRESHOLD * frame_h
    court_x_left   = frame_w * SPECTATOR_ZONE         # skip boxes centred left of this
    court_x_right  = frame_w * (1.0 - SPECTATOR_ZONE) # skip boxes centred right of this

    frame_detections: list[tuple[float, ActionType, float]] = []  # (time, action, conf)
    prev_keypoints: dict[int, np.ndarray] = {}  # person_idx → previous frame's keypoints

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % skip_frames != 0:
            frame_idx += 1
            continue

        t = frame_idx / fps
        results = model(frame, imgsz=imgsz, verbose=False)

        curr_keypoints: dict[int, np.ndarray] = {}

        for result in results:
            if result.keypoints is None:
                continue
            kps = result.keypoints.xy.cpu().numpy()  # (N, 17, 2)
            confs = result.keypoints.conf.cpu().numpy() if result.keypoints.conf is not None else None
            boxes = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None

            for person_idx in range(len(kps)):
                person_kps = kps[person_idx]  # (17, 2)
                if person_kps.shape[0] < 17:  # model can emit boxes with empty keypoints
                    continue
                person_conf = confs[person_idx] if confs is not None else None

                # Filter 1: bounding box guards — skip tiny detections (seated crowd)
                # and people at the lateral frame edges (line judges, refs)
                if boxes is not None and person_idx < len(boxes):
                    x1, y1, x2, y2 = boxes[person_idx]
                    if (y2 - y1) < frame_h * 0.07:          # shorter than 7% of frame = crowd
                        continue
                    box_cx = (x1 + x2) / 2
                    if box_cx < court_x_left or box_cx > court_x_right:  # sideline official
                        continue

                # Filter 2: Motion gate — require wrist movement between frames
                curr_keypoints[person_idx] = person_kps
                if person_idx in prev_keypoints:
                    prev_kps = prev_keypoints[person_idx]
                    wrist_velocities = []
                    for wi in (L_WRIST, R_WRIST):
                        dx = person_kps[wi][0] - prev_kps[wi][0]
                        dy = person_kps[wi][1] - prev_kps[wi][1]
                        wrist_velocities.append(np.sqrt(dx * dx + dy * dy))
                    max_velocity = max(wrist_velocities)
                    if max_velocity < motion_px:
                        continue  # Not enough movement — skip

                action, confidence = classify_action(person_kps, person_conf)
                if action != "unknown" and confidence >= MIN_CONFIDENCE:
                    frame_detections.append((t, action, confidence))

        prev_keypoints = curr_keypoints
        frame_idx += 1

    cap.release()
    logger.info("Raw frame detections: %d", len(frame_detections))

    detections = _merge_detections(frame_detections, total_frames / fps)
    logger.info("Final detections: %d", len(detections))
    return [{"start": d.start, "end": d.end, "action": d.action, "confidence": d.confidence}
            for d in detections]


def extract_keypoints(
    video_path: str,
    *,
    model_name: str = "yolov8s-pose.pt",
    imgsz: int = 1280,
    sample_fps: float = 3.0,
    device: str | None = None,
    max_seconds: float | None = None,
    progress_every: float = 60.0,
) -> dict:
    """
    Raw court-region keypoints, sampled at a fixed rate (CF-198).

    `run_detection` also runs pose over a whole video, but it returns *action*
    labels — the skeleton heuristics have already collapsed each frame to a
    class and thrown the geometry away. The dead-time condense stage wants the
    geometry: whether anyone is moving like a player, which is a question
    `classify_action` cannot answer and `MIN_CONFIDENCE` would filter away.
    So this is a second reader of the same model, not a rewrite of that path —
    deliberately, because regressing the shipping pose path to serve a
    dead-time experiment would be a bad trade.

    Two filters are shared with `run_detection` rather than re-invented, so the
    signal sees the same people the shipping path does: boxes shorter than 7% of
    the frame are seated crowd, and boxes centred outside the SPECTATOR_ZONE
    margins are line judges and officials.

    `sample_fps` is the cost lever. Stage 3 runs at 30/SKIP_FRAMES = 7.5/s to
    catch the instant of a spike; per-second player activity needs nothing like
    that, and the ball track this signal sits beside runs at 0.5-2.6 samples/s.
    Every sample is a GPU forward pass, so this number *is* the price.

    Returns a cache dict: metadata plus `samples`, one entry per sampled frame
    with every surviving person's box and 17 COCO keypoints. Coordinates stay in
    pixels — the consumer normalizes by `frame_height`, as the ball signal does.
    """
    from ultralytics import YOLO
    model = YOLO(_model_path(model_name))

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    duration = total_frames / fps if fps else 0.0

    # Round to whole frames: a fractional stride would drift the sample times
    # apart over an hour of footage, and the consumer takes differences between
    # consecutive samples.
    stride = max(1, int(round(fps / sample_fps))) if sample_fps > 0 else 1
    court_x_left = frame_w * SPECTATOR_ZONE
    court_x_right = frame_w * (1.0 - SPECTATOR_ZONE)
    logger.info(
        "Keypoint pass: %.1f fps, %dx%dpx, %.0fs — every %d frames (%.2f samples/s)",
        fps, frame_w, frame_h, duration, stride, fps / stride,
    )

    samples: list[dict] = []
    frame_idx = 0
    next_log = progress_every
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % stride != 0:
            frame_idx += 1
            continue

        t = frame_idx / fps
        if max_seconds is not None and t > max_seconds:
            break
        if t >= next_log:
            logger.info("  ... %.0fs / %.0fs (%d samples)", t, duration, len(samples))
            next_log += progress_every

        persons: list[dict] = []
        for result in model(frame, imgsz=imgsz, device=device, verbose=False):
            if result.keypoints is None:
                continue
            kps = result.keypoints.xy.cpu().numpy()          # (N, 17, 2)
            confs = result.keypoints.conf.cpu().numpy() if result.keypoints.conf is not None else None
            boxes = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None

            for i in range(len(kps)):
                person_kps = kps[i]
                if person_kps.shape[0] < 17:  # model can emit boxes with empty keypoints
                    continue
                if boxes is None or i >= len(boxes):
                    continue
                x1, y1, x2, y2 = (float(v) for v in boxes[i])
                if (y2 - y1) < frame_h * 0.07:               # seated crowd
                    continue
                if not court_x_left <= (x1 + x2) / 2 <= court_x_right:  # sideline official
                    continue
                persons.append({
                    # 1dp is well under the noise floor of a 1280px inference
                    # resize, and halves the cache on a full game.
                    "box": [round(v, 1) for v in (x1, y1, x2, y2)],
                    "kps": [[round(float(x), 1), round(float(y), 1)] for x, y in person_kps],
                    "conf": (
                        [round(float(c), 3) for c in confs[i]]
                        if confs is not None and i < len(confs) else None
                    ),
                })

        samples.append({"time": round(t, 3), "persons": persons})
        frame_idx += 1

    cap.release()
    logger.info(
        "Keypoint pass done: %d samples, %d person-detections",
        len(samples), sum(len(s["persons"]) for s in samples),
    )
    return {
        "model_name": model_name,
        "imgsz": imgsz,
        "sample_fps": fps / stride if stride else 0.0,
        "frame_width": frame_w,
        "frame_height": frame_h,
        "video_fps": fps,
        "duration": duration,
        "samples": samples,
    }

def classify_action(
    kps: np.ndarray,
    confs: np.ndarray | None,
    conf_threshold: float = 0.4,
) -> tuple[ActionType, float]:
    """
    Rule-based action classification from 17 COCO keypoints.

    Returns (action_type, confidence_estimate).
    """
    def visible(idx: int) -> bool:
        if confs is None:
            return True
        return bool(confs[idx] > conf_threshold)

    def y(idx: int) -> float:
        return float(kps[idx][1])

    def x(idx: int) -> float:
        return float(kps[idx][0])

    # Guard: need at least shoulders and wrists
    if not all(visible(i) for i in [L_SHOULDER, R_SHOULDER]):
        return "unknown", 0.0

    shoulder_y = (y(L_SHOULDER) + y(R_SHOULDER)) / 2
    hip_y = (y(L_HIP) + y(R_HIP)) / 2 if (visible(L_HIP) and visible(R_HIP)) else shoulder_y + 50

    body_height = abs(hip_y - shoulder_y) + 1e-6

    # ── Spike / serve heuristic ────────────────────────────────────────────────
    # One or both wrists are above the shoulder line
    wrist_above = 0
    if visible(L_WRIST) and y(L_WRIST) < shoulder_y:
        wrist_above += 1
    if visible(R_WRIST) and y(R_WRIST) < shoulder_y:
        wrist_above += 1

    if wrist_above >= 1:
        # Distinguish spike vs serve by elbow angle (rough heuristic)
        # Spike: arm extended upward (wrist above elbow above shoulder)
        elbow_above = 0
        if visible(L_ELBOW) and y(L_ELBOW) < shoulder_y:
            elbow_above += 1
        if visible(R_ELBOW) and y(R_ELBOW) < shoulder_y:
            elbow_above += 1

        if elbow_above >= 1 and wrist_above >= 2:
            # Both wrists + at least one elbow above shoulder = strong spike signal
            return "spike", 0.85
        elif elbow_above >= 1:
            return "spike", 0.65

        # Serve: only wrist above, elbow still below — weaker signal, require both wrists
        if wrist_above >= 2:
            return "serve", 0.60
        # Single wrist above shoulder is very common in idle poses — skip
        return "unknown", 0.0

    # ── Dig heuristic ──────────────────────────────────────────────────────────
    # Both wrists below hip level (platform pass position)
    # Tighter: wrists must be WELL below hips and close together
    if (visible(L_WRIST) and visible(R_WRIST)
            and y(L_WRIST) > hip_y + body_height * 0.2
            and y(R_WRIST) > hip_y + body_height * 0.2):
        wrist_dist = abs(x(L_WRIST) - x(R_WRIST))
        if wrist_dist < body_height * 0.4:
            return "dig", 0.60

    # ── Set heuristic ──────────────────────────────────────────────────────────
    # Both hands near face level, slightly above shoulders, close together
    face_y = y(NOSE) if visible(NOSE) else shoulder_y - 20
    if (visible(L_WRIST) and visible(R_WRIST)):
        lw_near_face = abs(y(L_WRIST) - face_y) < body_height * 0.25
        rw_near_face = abs(y(R_WRIST) - face_y) < body_height * 0.25
        wrist_spread = abs(x(L_WRIST) - x(R_WRIST))
        if lw_near_face and rw_near_face and wrist_spread < body_height * 0.5:
            return "set", 0.55

    # ── Block heuristic ───────────────────────────────────────────────────────
    # Both arms fully extended upward (both wrists AND elbows above shoulders)
    if (visible(L_WRIST) and visible(R_WRIST)
            and visible(L_ELBOW) and visible(R_ELBOW)
            and y(L_WRIST) < shoulder_y and y(R_WRIST) < shoulder_y
            and y(L_ELBOW) < shoulder_y and y(R_ELBOW) < shoulder_y):
        # Wrists spread wide (hands apart at net)
        wrist_spread = abs(x(L_WRIST) - x(R_WRIST))
        if wrist_spread > body_height * 0.5:
            return "block", 0.65

    return "unknown", 0.0


def _merge_detections(
    frame_dets: list[tuple[float, ActionType, float]],
    video_duration: float,
) -> list[Detection]:
    """Group nearby frame-level detections into single clips."""
    if not frame_dets:
        return []

    # Sort by time
    frame_dets.sort(key=lambda x: x[0])

    groups: list[list[tuple[float, ActionType, float]]] = []
    current_group: list[tuple[float, ActionType, float]] = [frame_dets[0]]

    for t, action, conf in frame_dets[1:]:
        if t - current_group[-1][0] <= MIN_CLIP_GAP:
            current_group.append((t, action, conf))
        else:
            groups.append(current_group)
            current_group = [(t, action, conf)]
    groups.append(current_group)

    detections = []
    for group in groups:
        # Filter: require minimum number of frame-level detections
        if len(group) < MIN_GROUP_FRAMES:
            continue

        # Pick dominant action by confidence
        action_scores: dict[str, float] = {}
        for _, action, conf in group:
            action_scores[action] = action_scores.get(action, 0) + conf

        best_action = max(action_scores, key=action_scores.__getitem__)  # type: ignore[arg-type]
        best_conf = min(action_scores[best_action] / len(group), 0.95)
        peak_time = max(group, key=lambda x: x[2])[0]

        d = Detection(peak_time=peak_time, action=best_action, confidence=best_conf)  # type: ignore[arg-type]
        d.end = min(d.end, video_duration)
        detections.append(d)

    return detections


def group_into_rallies(detections: list[dict], video_duration: float) -> list[dict]:
    """
    Merge per-action detections that belong to the same rally into single clips.

    Two detections are part of the same rally when the gap between one clip's
    end and the next clip's start is <= RALLY_GAP_SECONDS.  The resulting rally
    clip spans from the earliest start to the latest end (individual per-action
    padding is already baked in, so no additional padding is applied).

    Extra fields added to each output dict:
      labels   — ordered list of unique non-unknown action types in the rally
                 (e.g. ["spike", "dig", "set"])
      action   — dominant action by summed confidence
      confidence — mean confidence across all constituent detections
    """
    if not detections:
        return []

    sorted_dets = sorted(detections, key=lambda d: d["start"])

    # Group consecutive detections into rallies
    rallies: list[list[dict]] = []
    current: list[dict] = [sorted_dets[0]]

    for det in sorted_dets[1:]:
        gap = det["start"] - current[-1]["end"]
        if gap <= RALLY_GAP_SECONDS:
            current.append(det)
        else:
            rallies.append(current)
            current = [det]
    rallies.append(current)

    result: list[dict] = []
    for rally in rallies:
        rally_start = min(d["start"] for d in rally)
        rally_end = min(max(d["end"] for d in rally), video_duration)

        # Preserve insertion order of first-seen action types (skip unknown)
        seen: dict[str, None] = {}
        for d in rally:
            if d["action"] != "unknown":
                seen[d["action"]] = None
        labels = list(seen.keys())

        # Dominant action by summed confidence
        action_scores: dict[str, float] = {}
        for d in rally:
            if d["action"] != "unknown":
                action_scores[d["action"]] = action_scores.get(d["action"], 0.0) + d["confidence"]

        if action_scores:
            dominant = max(action_scores, key=action_scores.__getitem__)  # type: ignore[arg-type]
        else:
            dominant = "unknown"

        avg_conf = round(sum(d["confidence"] for d in rally) / len(rally), 4)

        result.append({
            "start": rally_start,
            "end": rally_end,
            "action": dominant,
            "confidence": avg_conf,
            "labels": labels,
        })

    logger.info(
        "Rally grouping: %d detections → %d rallies (avg %.1f actions/rally)",
        len(detections),
        len(result),
        len(detections) / max(len(result), 1),
    )
    return result


def classify_within_windows(
    video_path: str,
    windows: list[dict],
    model_name: str = "yolov8s-pose.pt",
    imgsz: int = 1280,
    skip_frames: int = SKIP_FRAMES,
) -> list[dict]:
    """
    Run pose classification only inside the given time windows (rally clips).

    Much faster than full-video scan because it seeks past all dead time.
    For each window, accumulates per-action confidence scores across frames,
    then picks the dominant action and merges it back into the window dict.

    If pose produces a higher-confidence label than the ball-trajectory label
    already in the window, the pose label wins. Otherwise the ball label is kept.

    Returns the same list with 'action', 'confidence', and 'labels' updated.

    Args:
        model_name:  YOLO pose model to use (default: yolov8s-pose.pt for quality;
                     use yolov8n-pose.pt for faster CPU testing).
        imgsz:       Inference resolution (default: 1280; use 640 for CPU testing).
        skip_frames: Process every Nth frame (default: SKIP_FRAMES=4; raise for speed).
    """
    if not windows:
        return windows

    try:
        from ultralytics import YOLO
        model = YOLO(_model_path(model_name))
    except ImportError:
        logger.warning("ultralytics not installed — skipping pose refinement")
        return windows

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning("classify_within_windows: cannot open %s", video_path)
        return windows

    fps      = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    frame_h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080

    court_x_left  = frame_w * SPECTATOR_ZONE
    court_x_right = frame_w * (1.0 - SPECTATOR_ZONE)
    motion_px     = MOTION_THRESHOLD * frame_h

    refined: list[dict] = []
    scored = 0            # windows that actually produced a pose signal
    decoded_any = False   # have we ever successfully read a frame?

    for window in windows:
        w_start = window["start"]
        w_end   = window["end"]

        cap.set(cv2.CAP_PROP_POS_MSEC, w_start * 1000)

        action_scores: dict[str, float] = {}
        prev_kps: dict[int, np.ndarray] = {}
        frame_idx = int(w_start * fps)

        frames_here = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                # Ambiguous on its own: true end-of-video, or a stream that has
                # stopped answering. Reading nothing at all in a window we have
                # seeked to, after earlier windows decoded fine, is the second
                # one — and it is silent, because every later window fails the
                # same way and still gets counted as "refined".
                if frames_here == 0 and decoded_any:
                    cap.release()
                    raise PoseStreamError(
                        f"decoder returned no frames for window "
                        f"{w_start:.1f}-{w_end:.1f}s after {scored} scored window(s); "
                        f"refinement cannot continue on this source"
                    )
                break
            frames_here += 1
            decoded_any = True
            pos_sec = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if pos_sec > w_end:
                break
            frame_idx += 1
            if frame_idx % skip_frames != 0:
                continue

            results = model(frame, imgsz=imgsz, verbose=False)
            for result in results:
                if result.keypoints is None:
                    continue
                kps_all  = result.keypoints.xy.cpu().numpy()
                conf_all = (result.keypoints.conf.cpu().numpy()
                            if result.keypoints.conf is not None else None)
                boxes    = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None

                for pi in range(len(kps_all)):
                    person_kps  = kps_all[pi]
                    if person_kps.shape[0] < 17:  # model can emit boxes with empty keypoints
                        continue
                    person_conf = conf_all[pi] if conf_all is not None else None

                    if boxes is not None and pi < len(boxes):
                        x1, y1, x2, y2 = boxes[pi]
                        if (y2 - y1) < frame_h * 0.07:
                            continue
                        box_cx = (x1 + x2) / 2
                        if box_cx < court_x_left or box_cx > court_x_right:
                            continue

                    if pi in prev_kps:
                        prev = prev_kps[pi]
                        vel = max(
                            np.hypot(person_kps[L_WRIST][0] - prev[L_WRIST][0],
                                     person_kps[L_WRIST][1] - prev[L_WRIST][1]),
                            np.hypot(person_kps[R_WRIST][0] - prev[R_WRIST][0],
                                     person_kps[R_WRIST][1] - prev[R_WRIST][1]),
                        )
                        if vel < motion_px:
                            prev_kps[pi] = person_kps
                            continue
                    prev_kps[pi] = person_kps

                    action, conf = classify_action(person_kps, person_conf)
                    if action != "unknown" and conf >= MIN_CONFIDENCE:
                        action_scores[action] = action_scores.get(action, 0.0) + conf

        out = dict(window)
        if action_scores:
            best_pose_action = max(action_scores, key=action_scores.__getitem__)
            total            = sum(action_scores.values())
            pose_conf        = round(min(action_scores[best_pose_action] / max(total, 1) * 0.92, 0.92), 3)

            ball_conf = window.get("confidence", 0.0)
            if pose_conf > ball_conf or window.get("action") == "unknown":
                out["action"]     = best_pose_action
                out["confidence"] = pose_conf
                seen: dict[str, None] = {}
                for a in sorted(action_scores, key=action_scores.__getitem__, reverse=True):
                    seen[a] = None
                out["labels"] = list(seen.keys())
                logger.debug(
                    "Window %.1f-%.1fs: pose overrides ball — %s (%.2f > %.2f)",
                    w_start, w_end, best_pose_action, pose_conf, ball_conf,
                )

        if action_scores:
            scored += 1
        refined.append(out)

    cap.release()
    # Report windows that produced a pose signal, not windows visited. The old
    # count was len(refined), which is just the input length — a run that decoded
    # nothing still read as a full success.
    logger.info(
        "classify_within_windows: pose signal on %d/%d windows", scored, len(refined),
    )
    if windows and scored == 0:
        logger.warning(
            "classify_within_windows: no window produced a pose signal — every clip "
            "keeps its trajectory label. Expected only if the footage genuinely has "
            "no visible players; otherwise the source or the model is at fault."
        )
    return refined


def fuse_with_ball_contacts(
    pose_detections: list[dict],
    ball_contacts: list[dict],
    video_duration: float,
) -> list[dict]:
    """
    Merge pose detections with ball contact timestamps to improve timing and
    reduce both false positives and false negatives.

    Ball contacts are ground truth for "a hit happened here."
    Pose detections provide action labels (spike / dig / set / ...).

    Rules:
    - Ball contact + nearby pose → confirmed clip anchored to contact time,
      pose label kept, confidence boosted.
    - Ball contact alone (no pose match) → clip created only if the contact
      is "strong" (high angle/speed change = powerful hit like spike or serve).
    - Pose alone (no ball contact nearby) → confidence penalised by
      NO_BALL_PENALTY; dropped if the result falls below MIN_CONFIDENCE.
    """
    contacts = sorted(ball_contacts, key=lambda c: c["time"])
    poses    = sorted(pose_detections, key=lambda d: d["start"])

    def _pose_peak(d: dict) -> float:
        return d["start"] + PAD_BEFORE

    used_pose_indices: set[int] = set()
    fused: list[dict] = []

    # ── Match each ball contact to the nearest pose detection ─────────────────
    for contact in contacts:
        tc = contact["time"]
        best_pi   = None
        best_dist = float("inf")
        for pi, pose in enumerate(poses):
            if pi in used_pose_indices:
                continue
            dist = abs(_pose_peak(pose) - tc)
            if dist < BALL_POSE_WINDOW and dist < best_dist:
                best_dist = dist
                best_pi   = pi

        if best_pi is not None:
            pose = poses[best_pi]
            used_pose_indices.add(best_pi)
            # Re-anchor clip window to the ball contact time for precision
            start = max(0.0, tc - PAD_BEFORE)
            end   = min(video_duration, tc + PAD_AFTER)
            fused.append({
                "start":      start,
                "end":        end,
                "action":     pose["action"],
                "confidence": round(min(pose["confidence"] + 0.15, 0.95), 4),
                "labels":     pose.get("labels", []),
            })
        else:
            # No pose match — only create a clip for strong contacts (powerful hits)
            is_strong = (
                contact.get("angle_change", 0) >= BALL_STRONG_ANGLE
                or contact.get("speed_change", 0) >= BALL_STRONG_SPEED
            )
            if is_strong:
                start = max(0.0, tc - PAD_BEFORE)
                end   = min(video_duration, tc + PAD_AFTER)
                fused.append({
                    "start":      start,
                    "end":        end,
                    "action":     "unknown",
                    "confidence": BALL_ONLY_CONF,
                    "labels":     [],
                })

    # ── Penalise pose detections that had no supporting ball contact ──────────
    for pi, pose in enumerate(poses):
        if pi in used_pose_indices:
            continue
        penalised_conf = round(pose["confidence"] * NO_BALL_PENALTY, 4)
        if penalised_conf >= MIN_CONFIDENCE:
            fused.append({
                "start":      pose["start"],
                "end":        pose["end"],
                "action":     pose["action"],
                "confidence": penalised_conf,
                "labels":     pose.get("labels", []),
            })

    fused.sort(key=lambda d: d["start"])
    logger.info(
        "Ball fusion: %d contacts + %d pose → %d fused detections",
        len(contacts), len(poses), len(fused),
    )
    return fused


def _stub_detections(video_path: str) -> list[dict]:
    """Fallback for environments without ultralytics (returns fake data for dev)."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    duration = total_frames / fps

    actions = ["spike", "serve", "dig", "set", "block"]
    # Generate ~10-15 clips max, spread across the video
    target_clips = min(15, max(3, int(duration / 60)))  # ~1 clip per minute, max 15
    interval = max(10, int(duration / target_clips))
    start_offset = min(5, duration * 0.05)
    return [
        {
            "start": max(0, t - 2),
            "end": min(duration, t + 3),
            "action": actions[i % len(actions)],
            "confidence": 0.75,
        }
        for i, t in enumerate(range(int(start_offset), max(int(start_offset) + 1, int(duration) - 2), interval))
    ]
