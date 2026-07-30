"""
Modal GPU function for YOLOv8-pose volleyball action detection.

Runs on a cloud GPU via Modal. Called by the Celery worker with an R2 key,
downloads the video, runs pose estimation + rule-based classification,
and returns a list of detections.

Deploy:   modal deploy ml/modal_detect.py
Test:     modal run ml/modal_detect.py
"""
from __future__ import annotations

import modal

# ── Modal app + container image ──────────────────────────────────────────────
app = modal.App("clipfarm-detect")

detect_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install(
        "ultralytics>=8.3",
        "opencv-python-headless",
        "numpy",
        "boto3",
    )
)


# ── YOLOv8 pose keypoint indices (COCO 17-point) ────────────────────────────
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# ── Detection config ─────────────────────────────────────────────────────────
SKIP_FRAMES = 4          # Analyse every Nth frame (4× speedup)
PAD_BEFORE = 2.0         # Seconds of context before action peak
PAD_AFTER = 3.0          # Seconds of context after action peak
MIN_CLIP_GAP = 1.5       # Merge detections closer than this (seconds)


def classify_action(kps, confs, conf_threshold=0.4):
    """
    Rule-based action classification from 17 COCO keypoints.
    Returns (action_type, confidence_estimate).
    """
    import numpy as np

    def visible(idx):
        if confs is None:
            return True
        return bool(confs[idx] > conf_threshold)

    def y(idx):
        return float(kps[idx][1])

    def x(idx):
        return float(kps[idx][0])

    # Need at least shoulders
    if not all(visible(i) for i in [L_SHOULDER, R_SHOULDER]):
        return "unknown", 0.0

    shoulder_y = (y(L_SHOULDER) + y(R_SHOULDER)) / 2
    hip_y = (
        (y(L_HIP) + y(R_HIP)) / 2
        if (visible(L_HIP) and visible(R_HIP))
        else shoulder_y + 50
    )
    body_height = abs(hip_y - shoulder_y) + 1e-6

    # ── Block: both arms fully extended upward, hands spread wide ─────────
    if (
        visible(L_WRIST) and visible(R_WRIST)
        and visible(L_ELBOW) and visible(R_ELBOW)
        and y(L_WRIST) < shoulder_y and y(R_WRIST) < shoulder_y
        and y(L_ELBOW) < shoulder_y and y(R_ELBOW) < shoulder_y
    ):
        wrist_spread = abs(x(L_WRIST) - x(R_WRIST))
        if wrist_spread > body_height * 0.5:
            return "block", 0.65

    # ── Spike: one/both wrists above shoulder, elbow also above ───────────
    wrist_above = 0
    if visible(L_WRIST) and y(L_WRIST) < shoulder_y:
        wrist_above += 1
    if visible(R_WRIST) and y(R_WRIST) < shoulder_y:
        wrist_above += 1

    if wrist_above >= 1:
        elbow_above = 0
        if visible(L_ELBOW) and y(L_ELBOW) < shoulder_y:
            elbow_above += 1
        if visible(R_ELBOW) and y(R_ELBOW) < shoulder_y:
            elbow_above += 1

        if elbow_above >= 1:
            # Check if only one arm is up (spike) vs both (already caught by block)
            highest_wrist_y = min(
                y(L_WRIST) if visible(L_WRIST) else 9999,
                y(R_WRIST) if visible(R_WRIST) else 9999,
            )
            height_above = shoulder_y - highest_wrist_y
            conf = min(0.55 + 0.25 * (height_above / body_height), 0.90)
            return "spike", conf

        # Wrist above but elbow isn't — more like a serve toss
        return "serve", 0.55

    # ── Set: both hands near face level, close together ───────────────────
    face_y = y(NOSE) if visible(NOSE) else shoulder_y - 20
    if visible(L_WRIST) and visible(R_WRIST):
        lw_near_face = abs(y(L_WRIST) - face_y) < body_height * 0.35
        rw_near_face = abs(y(R_WRIST) - face_y) < body_height * 0.35
        wrist_spread = abs(x(L_WRIST) - x(R_WRIST))
        if lw_near_face and rw_near_face and wrist_spread < body_height * 0.6:
            return "set", 0.50

    # ── Dig: both wrists below hip, close together (platform) ────────────
    if visible(L_WRIST) and visible(R_WRIST):
        if y(L_WRIST) > hip_y and y(R_WRIST) > hip_y:
            wrist_dist = abs(x(L_WRIST) - x(R_WRIST))
            if wrist_dist < body_height * 0.5:
                # Check for low body position (knees bent)
                knee_below_hip = False
                if visible(L_KNEE) and visible(R_KNEE):
                    knee_y = (y(L_KNEE) + y(R_KNEE)) / 2
                    knee_below_hip = knee_y > hip_y
                conf = 0.65 if knee_below_hip else 0.50
                return "dig", conf

    return "unknown", 0.0


def merge_detections(frame_dets, video_duration):
    """Group nearby frame-level detections into single clips."""
    if not frame_dets:
        return []

    frame_dets.sort(key=lambda x: x[0])

    groups = []
    current_group = [frame_dets[0]]

    for t, action, conf in frame_dets[1:]:
        if t - current_group[-1][0] <= MIN_CLIP_GAP:
            current_group.append((t, action, conf))
        else:
            groups.append(current_group)
            current_group = [(t, action, conf)]
    groups.append(current_group)

    detections = []
    for group in groups:
        action_scores = {}
        for _, action, conf in group:
            action_scores[action] = action_scores.get(action, 0) + conf

        best_action = max(action_scores, key=action_scores.__getitem__)
        best_conf = min(action_scores[best_action] / len(group), 0.95)
        peak_time = max(group, key=lambda x: x[2])[0]

        start = max(0.0, peak_time - PAD_BEFORE)
        end = min(video_duration, peak_time + PAD_AFTER)
        detections.append({
            "start": start,
            "end": end,
            "action": best_action,
            "confidence": round(best_conf, 3),
        })

    return detections


@app.function(
    image=detect_image,
    gpu="T4",
    timeout=600,
    secrets=[modal.Secret.from_name("clipfarm-r2")],
)
def detect_actions(r2_key: str) -> list[dict]:
    """
    Download video from R2, run YOLOv8-pose on GPU, return detections.

    Args:
        r2_key: The R2 object key for the raw video (e.g. "raw/<game_id>.mp4")

    Returns:
        List of dicts: [{start, end, action, confidence}, ...]
    """
    import os
    import tempfile
    import logging

    import boto3
    import cv2
    from botocore.config import Config
    from ultralytics import YOLO

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("modal_detect")

    # ── Download video from R2 ────────────────────────────────────────────
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        video_path = f.name

    logger.info("Downloading %s from R2...", r2_key)
    s3.download_file(os.environ["R2_BUCKET_NAME"], r2_key, video_path)

    # ── Load YOLOv8-pose model ────────────────────────────────────────────
    logger.info("Loading YOLOv8-pose model...")
    model = YOLO("yolov8n-pose.pt")

    # ── Process video ─────────────────────────────────────────────────────
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    logger.info("Video: %.1f fps, %d frames (%.0fs)", fps, total_frames, duration)

    frame_detections = []  # (time, action, confidence)
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % SKIP_FRAMES != 0:
            frame_idx += 1
            continue

        t = frame_idx / fps
        results = model(frame, verbose=False)

        for result in results:
            if result.keypoints is None:
                continue
            kps = result.keypoints.xy.cpu().numpy()
            confs = (
                result.keypoints.conf.cpu().numpy()
                if result.keypoints.conf is not None
                else None
            )

            for person_idx in range(len(kps)):
                action, confidence = classify_action(
                    kps[person_idx], confs[person_idx] if confs is not None else None
                )
                if action != "unknown":
                    frame_detections.append((t, action, confidence))

        frame_idx += 1

        # Log progress every 30s of video
        if frame_idx % (int(fps) * 30) == 0:
            logger.info("Progress: %.0f / %.0fs", t, duration)

    cap.release()
    os.unlink(video_path)

    logger.info("Raw frame detections: %d", len(frame_detections))
    detections = merge_detections(frame_detections, duration)
    logger.info("Merged into %d clips", len(detections))

    return detections


# ── CLI entry point for testing ──────────────────────────────────────────────
@app.local_entrypoint()
def main():
    """Test with: modal run ml/modal_detect.py"""
    print("Modal detect function deployed successfully!")
    print("Call detect_actions.remote(r2_key) from your worker.")
