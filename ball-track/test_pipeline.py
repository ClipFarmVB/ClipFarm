"""
End-to-end pipeline test on test_long.mp4.

Runs:
  1. Ball tracking  → contacts with trajectory-based action labels
  2. Rally grouping → clip windows
  3. Pose within windows → refined labels
  4. Prints a summary table

No Celery, no R2, no database required.

Ball tracking results are cached to test_long_cache.json so the slow
Roboflow API call only runs once.
"""
import json
import os
import sys
import time
import cv2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Allow importing from the project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.pipeline.ball import track_ball, find_contacts, contacts_to_rallies, BallPosition, TrackedBall
from ml.pipeline.detect import classify_within_windows

VIDEO = "test_long.mp4"
CACHE = "test_long_cache.json"
SAMPLE_EVERY = 10  # ≈3 fps from 30fps — same as production

# ─────────────────────────────────────────────────────────────────────────────

api_key = os.environ.get("ROBOFLOW_API_KEY", "")
if not api_key:
    sys.exit("ROBOFLOW_API_KEY not set — check .env")

cap = cv2.VideoCapture(VIDEO)
fps      = cap.get(cv2.CAP_PROP_FPS) or 30.0
frames   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
frame_h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
frame_w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
duration = frames / fps
cap.release()

print(f"Video : {VIDEO}")
print(f"        {frame_w}x{frame_h}  {fps:.0f}fps  {duration:.1f}s ({duration/60:.1f} min)\n")

# ── Stage 1: Ball tracking (cached) ──────────────────────────────────────────
print("Stage 1: Ball tracking...")
cache_path = Path(CACHE)
if cache_path.exists():
    print(f"  Loading cached positions from {CACHE}...")
    with open(cache_path) as f:
        cached = json.load(f)
    tracker = TrackedBall()
    for p in cached["positions"]:
        tracker.positions.append(BallPosition(**p))
    print(f"  Loaded {len(tracker.positions)} cached ball positions")
else:
    t0 = time.time()
    tracker = track_ball(VIDEO, api_key, sample_every=SAMPLE_EVERY)
    elapsed = time.time() - t0
    print(f"  Tracked {len(tracker.positions)} ball positions in {elapsed:.1f}s")
    with open(cache_path, "w") as f:
        json.dump({
            "positions": [
                {"frame": p.frame, "time": p.time, "x": p.x, "y": p.y, "confidence": p.confidence}
                for p in tracker.positions
            ]
        }, f)
    print(f"  Saved cache to {CACHE}")

# ── Stage 2: Contact detection + trajectory classification ────────────────────
print("\nStage 2: Contact detection + trajectory classification...")
contacts = find_contacts(tracker, frame_height=frame_h)
print(f"  Found {len(contacts)} contacts")

action_counts: dict[str, int] = {}
for c in contacts:
    a = c["action"]
    action_counts[a] = action_counts.get(a, 0) + 1

for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
    print(f"    {action:10s}  {count} contacts")

# Show gap distribution between consecutive contacts
if len(contacts) > 1:
    gaps = [contacts[i+1]["time"] - contacts[i]["time"] for i in range(len(contacts)-1)]
    gaps.sort()
    print(f"\n  Contact gap stats:")
    print(f"    min={gaps[0]:.1f}s  median={gaps[len(gaps)//2]:.1f}s  p90={gaps[int(len(gaps)*0.9)]:.1f}s  max={gaps[-1]:.1f}s")
    large_gaps = [(contacts[i]["time"], contacts[i+1]["time"])
                  for i in range(len(contacts)-1)
                  if contacts[i+1]["time"] - contacts[i]["time"] > 5.0]
    print(f"    Gaps >5s: {len(large_gaps)}")
    for a, b in large_gaps[:10]:
        print(f"      {a:.1f}s -> {b:.1f}s  (gap {b-a:.1f}s)")

# ── Stage 3: Rally grouping ───────────────────────────────────────────────────
print("\nStage 3: Rally grouping...")
rallies = contacts_to_rallies(contacts, duration, frame_h)
print(f"  {len(rallies)} rallies from {len(contacts)} contacts\n")

print(f"  {'#':>3}  {'Start':>7}  {'End':>7}  {'Len':>5}  {'Action':<10}  {'Conf':>5}  Labels")
print(f"  {'-'*3}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*10}  {'-'*5}  {'-'*30}")
for i, r in enumerate(rallies):
    length = r["end"] - r["start"]
    labels = ", ".join(r["labels"]) if r["labels"] else "-"
    print(f"  {i+1:>3}  {r['start']:>7.1f}  {r['end']:>7.1f}  {length:>5.1f}s  {r['action']:<10}  {r['confidence']:>5.2f}  {labels}")

# ── Stage 4: Pose refinement ──────────────────────────────────────────────────
# Use lighter settings for local CPU testing.
# Production (GPU) uses the defaults: yolov8s-pose + imgsz=1280 + skip_frames=4.
print("\nStage 4: Pose refinement within rally windows...")
t0 = time.time()
refined = classify_within_windows(
    VIDEO, rallies,
    model_name="yolov8n-pose.pt",  # nano = ~4x faster than small on CPU
    imgsz=640,                      # 640 vs 1280 = ~4x faster (FLOPS ∝ area)
    skip_frames=15,                 # 2fps from 30fps = enough for action class
)
print(f"  Done in {time.time()-t0:.1f}s\n")

print(f"  {'#':>3}  {'Start':>7}  {'End':>7}  {'Action (ball)':<14}  {'Action (final)':<14}  {'Conf':>5}")
print(f"  {'-'*3}  {'-'*7}  {'-'*7}  {'-'*14}  {'-'*14}  {'-'*5}")
for i, (orig, ref) in enumerate(zip(rallies, refined)):
    changed = " <" if ref["action"] != orig["action"] else ""
    print(f"  {i+1:>3}  {orig['start']:>7.1f}  {orig['end']:>7.1f}  {orig['action']:<14}  {ref['action']:<14}  {ref['confidence']:>5.2f}{changed}")

print(f"\nTotal rally time: {sum(r['end']-r['start'] for r in refined):.1f}s  "
      f"({sum(r['end']-r['start'] for r in refined)/duration*100:.0f}% of footage)")
