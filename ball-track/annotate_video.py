"""
Ball detection + annotation using Roboflow local inference.

On first run the model weights are downloaded from Roboflow and cached at
~/.inference/cache/ — subsequent runs are fully offline and fast.
"""
import os
import time
import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_VIDEO  = "test_spike.mp4"
OUTPUT_VIDEO = "annotated_spike.mp4"
API_KEY      = os.getenv("ROBOFLOW_API_KEY")
MODEL_ID     = "volleyball-ball-tracking-0eo7r/2"   # version 2 = 95% mAP

# Run inference on every Nth frame (3 = 10 fps from a 30 fps source)
SAMPLE_EVERY = 3
# Confidence threshold — detections below this are ignored
MIN_CONF     = 0.40
# ─────────────────────────────────────────────────────────────────────────────

print(f"Loading model {MODEL_ID} (downloads weights on first run)...")
from inference import get_model
model = get_model(MODEL_ID, api_key=API_KEY)
print("Model ready.\n")

cap = cv2.VideoCapture(INPUT_VIDEO)
if not cap.isOpened():
    raise FileNotFoundError(f"Could not open video: {INPUT_VIDEO}")

native_fps   = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
duration_s   = total_frames / native_fps

print(f"Video : {width}x{height} @ {native_fps:.1f}fps | {duration_s:.1f}s | {total_frames} frames")
print(f"Sampling every {SAMPLE_EVERY} frames -> ~{total_frames // SAMPLE_EVERY} inferences\n")

fourcc = cv2.VideoWriter_fourcc(*"mp4v")
out    = cv2.VideoWriter(OUTPUT_VIDEO, fourcc, native_fps, (width, height))

# Max distance (px) the ball can travel between sampled frames.
# Detections further than this from the last known position are treated
# as a different object (spare ball, player, crowd artifact).
MAX_JUMP_PX = 300

frame_idx  = 0
written    = 0
detected   = 0
misses     = 0
MAX_MISS   = 5           # reset track after this many consecutive missed frames
last_x: float | None = None
last_y: float | None = None
t_start    = time.time()

while True:
    ret, frame = cap.read()
    if not ret:
        break

    if frame_idx % SAMPLE_EVERY == 0:
        # Run local inference
        results = model.infer(frame, confidence=MIN_CONF)

        preds = []
        if results and hasattr(results[0], "predictions"):
            preds = results[0].predictions
        # Sort by confidence descending so we fall back to best if no track yet
        preds = sorted(preds, key=lambda p: p.confidence, reverse=True)

        # ── Trajectory filter ──────────────────────────────────────────────
        # When multiple balls are detected, pick the one closest to where
        # the tracked ball was last seen.  Reject anything beyond MAX_JUMP_PX
        # (spare balls sitting at the edge of the frame, crowd artifacts).
        best = None
        if preds:
            if last_x is None:
                best = preds[0]          # no track yet — highest confidence wins
            else:
                closest = min(preds, key=lambda p: np.hypot(p.x - last_x, p.y - last_y))
                if np.hypot(closest.x - last_x, closest.y - last_y) <= MAX_JUMP_PX:
                    best = closest

        if best:
            detected += 1
            misses = 0
            last_x, last_y = best.x, best.y
            x1 = int(best.x - best.width / 2)
            y1 = int(best.y - best.height / 2)
            x2 = int(best.x + best.width / 2)
            y2 = int(best.y + best.height / 2)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, f"{best.confidence:.2f}", (x1, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            print(f"  Frame {frame_idx:5d} | ball at ({best.x:.0f}, {best.y:.0f}) "
                  f"| conf {best.confidence:.2f}")
        else:
            misses += 1
            if misses >= MAX_MISS:
                last_x = last_y = None   # reset track — ball left frame or hidden
                misses = 0
            print(f"  Frame {frame_idx:5d} | no detection")

    out.write(frame)
    written += 1
    frame_idx += 1

cap.release()
out.release()

elapsed = time.time() - t_start
print(f"\nDone in {elapsed:.1f}s | {written} frames written -> {OUTPUT_VIDEO}")
print(f"Detected ball in {detected}/{total_frames // SAMPLE_EVERY} sampled frames "
      f"({100 * detected / max(total_frames // SAMPLE_EVERY, 1):.0f}%)")
