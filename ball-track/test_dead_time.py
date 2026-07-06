"""
Dead-time removal test on test_long.mp4.

Runs:
  1. Ball tracking (cached)   → contacts
  2. Keep-window derivation   → active windows (dead time = the gaps)
  3. Condensed video stitch   → dead_time_condensed.mp4
  4. Prints window table + original vs condensed durations

No Celery, no R2, no database required. Reuses test_long_cache.json from
test_pipeline.py so no Roboflow call is needed when the cache exists.
"""
import json
import os
import sys
import cv2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Allow importing from the project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.pipeline.ball import track_ball, find_contacts, BallPosition, TrackedBall
from ml.pipeline.dead_time import active_windows_from_contacts
from ml.pipeline.clip import generate_condensed_video

VIDEO = "test_long.mp4"
CACHE = "test_long_cache.json"
OUTPUT_DIR = Path("dead_time_out")
SAMPLE_EVERY = 10  # ≈3 fps from 30fps — same as production

# ─────────────────────────────────────────────────────────────────────────────

if not Path(VIDEO).exists():
    sys.exit(f"{VIDEO} not found — drop a game video here (gitignored)")

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
    tracker = TrackedBall(positions=[BallPosition(**p) for p in cached["positions"]])
else:
    api_key = os.environ.get("ROBOFLOW_API_KEY", "")
    if not api_key:
        sys.exit("ROBOFLOW_API_KEY not set and no cache — check .env")
    tracker = track_ball(VIDEO, api_key, sample_every=SAMPLE_EVERY)
    with open(cache_path, "w") as f:
        json.dump({"positions": [vars(p) for p in tracker.positions]}, f)

contacts = find_contacts(tracker, frame_height=frame_h)
print(f"  {len(tracker.positions)} positions → {len(contacts)} contacts\n")

# ── Stage 2: Keep windows ─────────────────────────────────────────────────────
print("Stage 2: Keep-window derivation...")
windows = active_windows_from_contacts(contacts, duration)

kept = sum(e - s for s, e in windows)
print(f"  {'#':>3}  {'start':>8}  {'end':>8}  {'length':>7}")
for i, (s, e) in enumerate(windows):
    print(f"  {i:>3}  {s:>8.1f}  {e:>8.1f}  {e - s:>6.1f}s")
print(f"\n  {len(windows)} windows, {kept:.1f}s kept of {duration:.1f}s "
      f"({100 * kept / duration:.0f}% — {100 * (1 - kept / duration):.0f}% dead time)\n")

# ── Stage 3: Stitch ──────────────────────────────────────────────────────────
print("Stage 3: Condensed video...")
OUTPUT_DIR.mkdir(exist_ok=True)
condensed_path, thumb_path, condensed_duration = generate_condensed_video(
    VIDEO, windows, OUTPUT_DIR,
)
print(f"  {condensed_path}  ({condensed_duration:.1f}s, thumb: {thumb_path})")
print(f"\n  Original {duration/60:.1f} min → condensed {condensed_duration/60:.1f} min")
