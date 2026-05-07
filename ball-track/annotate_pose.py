"""
Pose detection + action classification annotation.

Runs YOLOv8-pose on every frame of the input video, draws the skeleton,
and overlays the classified action label + confidence for each person.
"""
import sys
import time
import cv2
import numpy as np
from ultralytics import YOLO

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_VIDEO  = "test_spike.mp4"
OUTPUT_VIDEO = "annotated_pose.mp4"
MODEL        = "yolov8s-pose.pt"
SKIP_FRAMES  = 1
MIN_CONF_KP  = 0.25  # keypoint visibility threshold
DETECT_CONF  = 0.20  # person box confidence
INFER_SIZE   = 1280  # inference resolution — 1280 catches far-side players that 640 misses
MIN_BOX_H    = 80    # pixels — skip detections shorter than this (seated spectators, small crowd)
# refs stand at the net post; exclude a margin on each side of the frame
# set to 0.0/1.0 to disable (adjust per camera angle)
COURT_X_MIN  = 0.08  # fraction of frame width — left boundary of court area
COURT_X_MAX  = 0.92  # fraction of frame width — right boundary of court area
# ─────────────────────────────────────────────────────────────────────────────

# COCO 17-point keypoint indices
NOSE = 0
L_EYE, R_EYE = 1, 2
L_EAR, R_EAR = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

SKELETON = [
    (NOSE, L_EYE), (NOSE, R_EYE),
    (L_EYE, L_EAR), (R_EYE, R_EAR),
    (L_SHOULDER, R_SHOULDER),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST),
    (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
    (L_HIP, R_HIP),
    (L_HIP, L_KNEE), (L_KNEE, L_ANKLE),
    (R_HIP, R_KNEE), (R_KNEE, R_ANKLE),
]

ACTION_COLORS = {
    "spike":   (0,  200, 255),   # amber
    "serve":   (0,  255, 128),   # green
    "dig":     (255, 160,  0),   # blue
    "set":     (200,   0, 255),  # purple
    "block":   (0,  255, 255),   # yellow
    "unknown": (160, 160, 160),  # grey
}


def classify_action(kps: np.ndarray, confs: np.ndarray | None) -> tuple[str, float]:
    def vis(i):
        return confs is None or bool(confs[i] > MIN_CONF_KP)
    def y(i): return float(kps[i][1])
    def x(i): return float(kps[i][0])

    if not (vis(L_SHOULDER) and vis(R_SHOULDER)):
        return "unknown", 0.0

    shoulder_y = (y(L_SHOULDER) + y(R_SHOULDER)) / 2
    hip_y = ((y(L_HIP) + y(R_HIP)) / 2
             if (vis(L_HIP) and vis(R_HIP)) else shoulder_y + 50)
    body_h = abs(hip_y - shoulder_y) + 1e-6

    wrist_above = sum([
        vis(L_WRIST) and y(L_WRIST) < shoulder_y,
        vis(R_WRIST) and y(R_WRIST) < shoulder_y,
    ])

    if wrist_above >= 1:
        elbow_above = sum([
            vis(L_ELBOW) and y(L_ELBOW) < shoulder_y,
            vis(R_ELBOW) and y(R_ELBOW) < shoulder_y,
        ])
        if elbow_above >= 1 and wrist_above >= 2:
            return "spike", 0.85
        elif elbow_above >= 1:
            return "spike", 0.65
        if wrist_above >= 2:
            return "serve", 0.60
        return "unknown", 0.0

    if (vis(L_WRIST) and vis(R_WRIST)
            and y(L_WRIST) > hip_y + body_h * 0.2
            and y(R_WRIST) > hip_y + body_h * 0.2):
        if abs(x(L_WRIST) - x(R_WRIST)) < body_h * 0.4:
            return "dig", 0.60

    face_y = y(NOSE) if vis(NOSE) else shoulder_y - 20
    if vis(L_WRIST) and vis(R_WRIST):
        if (abs(y(L_WRIST) - face_y) < body_h * 0.25
                and abs(y(R_WRIST) - face_y) < body_h * 0.25
                and abs(x(L_WRIST) - x(R_WRIST)) < body_h * 0.5):
            return "set", 0.55

    if (vis(L_WRIST) and vis(R_WRIST) and vis(L_ELBOW) and vis(R_ELBOW)
            and y(L_WRIST) < shoulder_y and y(R_WRIST) < shoulder_y
            and y(L_ELBOW) < shoulder_y and y(R_ELBOW) < shoulder_y
            and abs(x(L_WRIST) - x(R_WRIST)) > body_h * 0.5):
        return "block", 0.65

    return "unknown", 0.0


def draw_skeleton(frame, kps, confs, color):
    for a, b in SKELETON:
        if confs is not None and (confs[a] < MIN_CONF_KP or confs[b] < MIN_CONF_KP):
            continue
        xa, ya = int(kps[a][0]), int(kps[a][1])
        xb, yb = int(kps[b][0]), int(kps[b][1])
        if xa == 0 and ya == 0: continue
        if xb == 0 and yb == 0: continue
        cv2.line(frame, (xa, ya), (xb, yb), color, 2, cv2.LINE_AA)

    for i, (px, py) in enumerate(kps):
        if confs is not None and confs[i] < MIN_CONF_KP:
            continue
        if px == 0 and py == 0:
            continue
        cv2.circle(frame, (int(px), int(py)), 4, color, -1, cv2.LINE_AA)
        cv2.circle(frame, (int(px), int(py)), 4, (255, 255, 255), 1, cv2.LINE_AA)


def draw_label(frame, text, x, y, color):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
    cv2.rectangle(frame, (x - 4, y - th - 6), (x + tw + 4, y + 4),
                  (0, 0, 0), -1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, color, 2, cv2.LINE_AA)


# ─────────────────────────────────────────────────────────────────────────────

print(f"Loading {MODEL}...")
model = YOLO(MODEL)
print("Model ready.\n")

cap = cv2.VideoCapture(INPUT_VIDEO)
if not cap.isOpened():
    sys.exit(f"Cannot open video: {INPUT_VIDEO}")

fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

print(f"Video : {w}x{h} @ {fps:.1f}fps | {total_frames/fps:.1f}s | {total_frames} frames")

out = cv2.VideoWriter(OUTPUT_VIDEO, cv2.VideoWriter_fourcc(*"mp4v"),
                      fps, (w, h))

t0 = time.time()
frame_idx = 0
action_counts: dict[str, int] = {}

while True:
    ret, frame = cap.read()
    if not ret:
        break

    if frame_idx % SKIP_FRAMES == 0:
        results = model(frame, conf=DETECT_CONF, imgsz=INFER_SIZE, verbose=False)

        court_x_min_px = w * COURT_X_MIN
        court_x_max_px = w * COURT_X_MAX

        for result in results:
            if result.keypoints is None:
                continue
            kps_all  = result.keypoints.xy.cpu().numpy()   # (N, 17, 2)
            conf_all = (result.keypoints.conf.cpu().numpy()
                        if result.keypoints.conf is not None else None)
            boxes    = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None

            for pi in range(len(kps_all)):
                kps   = kps_all[pi]
                confs = conf_all[pi] if conf_all is not None else None

                # Filter by bounding box: skip tiny detections and sideline officials
                if boxes is not None and pi < len(boxes):
                    x1, y1, x2, y2 = boxes[pi]
                    box_h    = y2 - y1
                    box_cx   = (x1 + x2) / 2
                    if box_h < MIN_BOX_H:
                        continue  # seated spectator / tiny crowd detection
                    if box_cx < court_x_min_px or box_cx > court_x_max_px:
                        continue  # sideline official (ref, line judge)

                action, conf = classify_action(kps, confs)
                color = ACTION_COLORS.get(action, ACTION_COLORS["unknown"])

                draw_skeleton(frame, kps, confs, color)

                # Label above shoulder midpoint
                lx = int((kps[L_SHOULDER][0] + kps[R_SHOULDER][0]) / 2)
                ly = int(min(kps[L_SHOULDER][1], kps[R_SHOULDER][1])) - 12
                ly = max(ly, 20)
                if action != "unknown":
                    draw_label(frame, f"{action}  {int(conf*100)}%", lx - 40, ly, color)
                    action_counts[action] = action_counts.get(action, 0) + 1

        # Frame counter overlay
        t = frame_idx / fps
        cv2.putText(frame, f"{t:.2f}s  f{frame_idx}", (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    out.write(frame)
    frame_idx += 1

cap.release()
out.release()

elapsed = time.time() - t0
print(f"\nDone in {elapsed:.1f}s | {frame_idx} frames written -> {OUTPUT_VIDEO}")
print("Action detections per frame:")
for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
    print(f"  {action:10s} {count} frames")
