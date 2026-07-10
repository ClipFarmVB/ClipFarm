"""
Dead-time removal test with baseline comparison.

Runs:
  1. Ball tracking (cached)   → contacts
  2. Keep-window derivation   → active windows (dead time = the gaps),
                                 computed twice: CF-24 baseline thresholds
                                 vs current defaults / CLI overrides
  3. Diff                     → segments the new thresholds keep that the
                                 baseline cut (the footage to eyeball)
  4. Condensed video stitch   → dead_time_out/condensed.mp4 (skipped when
                                 the video file is absent — stages 1-3 only
                                 need the cache)

No Celery, no R2, no database required. Reuses the tracking cache from
test_pipeline.py so no Roboflow call is needed when the cache exists.

Usage:
  python test_dead_time.py                       # defaults vs baseline
  python test_dead_time.py --gap-seconds 8       # try a candidate value
  python test_dead_time.py --video ../10mins.mp4 --cache tenmin_cache.json
"""
import argparse
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
from ml.pipeline.dead_time import active_windows_from_contacts, bridge_windows_by_motion
from ml.pipeline.clip import generate_condensed_video

OUTPUT_DIR = Path("dead_time_out")
SAMPLE_EVERY = 10  # ≈3 fps from 30fps — same as production

# CF-24 shipped thresholds — the "too aggressive" reference point (CF-46).
BASELINE = dict(
    gap_seconds=5.0,
    pad_before=2.0,
    pad_after=2.5,
    min_contacts=2,
    merge_gap_seconds=1.5,
)

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--video", default="test_long.mp4")
parser.add_argument("--cache", default="test_long_cache.json")
parser.add_argument("--gap-seconds", type=float, default=None)
parser.add_argument("--pad-before", type=float, default=None)
parser.add_argument("--pad-after", type=float, default=None)
parser.add_argument("--min-contacts", type=int, default=None)
parser.add_argument("--merge-gap-seconds", type=float, default=None)
parser.add_argument("--bridge-speed", type=float, default=None)
parser.add_argument("--bridge-fraction", type=float, default=None)
parser.add_argument("--max-bridge", type=float, default=None)
parser.add_argument("--no-bridge", action="store_true", help="skip the motion bridge")
args = parser.parse_args()

# Only pass explicitly-given overrides so the function defaults stay in play.
overrides = {
    k: v for k, v in {
        "gap_seconds": args.gap_seconds,
        "pad_before": args.pad_before,
        "pad_after": args.pad_after,
        "min_contacts": args.min_contacts,
        "merge_gap_seconds": args.merge_gap_seconds,
    }.items() if v is not None
}
bridge_overrides = {
    k: v for k, v in {
        "speed_pxps": args.bridge_speed,
        "fast_fraction": args.bridge_fraction,
        "max_bridge_seconds": args.max_bridge,
    }.items() if v is not None
}


def subtract_intervals(keep, cut):
    """Pieces of `keep` intervals not covered by any `cut` interval."""
    pieces = []
    for start, end in keep:
        spans = [(start, end)]
        for c_start, c_end in cut:
            next_spans = []
            for s, e in spans:
                if c_end <= s or c_start >= e:
                    next_spans.append((s, e))
                    continue
                if c_start > s:
                    next_spans.append((s, c_start))
                if c_end < e:
                    next_spans.append((c_end, e))
            spans = next_spans
        pieces.extend(spans)
    return [(s, e) for s, e in pieces if e - s > 0.05]


def print_windows(label, windows, duration):
    kept = sum(e - s for s, e in windows)
    print(f"  {label}")
    print(f"  {'#':>3}  {'start':>8}  {'end':>8}  {'length':>7}")
    for i, (s, e) in enumerate(windows):
        print(f"  {i:>3}  {s:>8.1f}  {e:>8.1f}  {e - s:>6.1f}s")
    print(f"  {len(windows)} windows, {kept:.1f}s kept of {duration:.1f}s "
          f"({100 * kept / duration:.0f}% — {100 * (1 - kept / duration):.0f}% dead time)\n")
    return kept


video_path = Path(args.video)
cache_path = Path(args.cache)
have_video = video_path.exists()

if not have_video and not cache_path.exists():
    sys.exit(f"Neither {args.video} nor {args.cache} found — need a video "
             f"(gitignored) or a tracking cache from test_pipeline.py")

if have_video:
    cap = cv2.VideoCapture(str(video_path))
    fps      = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    duration = frames / fps
    cap.release()
    print(f"Video : {args.video}")
    print(f"        {frame_w}x{frame_h}  {fps:.0f}fps  {duration:.1f}s ({duration/60:.1f} min)\n")
else:
    print(f"Video : {args.video} not found — cache-only run (no stitch)\n")
    frame_h = None  # derived from the cache below
    duration = None

# ── Stage 1: Ball tracking (cached) ──────────────────────────────────────────
print("Stage 1: Ball tracking...")
if cache_path.exists():
    print(f"  Loading cached positions from {args.cache}...")
    with open(cache_path) as f:
        cached = json.load(f)
    tracker = TrackedBall(positions=[BallPosition(**p) for p in cached["positions"]])
else:
    api_key = os.environ.get("ROBOFLOW_API_KEY", "")
    if not api_key:
        sys.exit("ROBOFLOW_API_KEY not set and no cache — check .env")
    tracker = track_ball(str(video_path), api_key, sample_every=SAMPLE_EVERY)
    with open(cache_path, "w") as f:
        json.dump({"positions": [vars(p) for p in tracker.positions]}, f)

if frame_h is None:
    frame_h = int(max(p.y for p in tracker.positions)) + 1
if duration is None:
    duration = max(p.time for p in tracker.positions)

contacts = find_contacts(tracker, frame_height=frame_h)
print(f"  {len(tracker.positions)} positions → {len(contacts)} contacts\n")

# ── Stage 2: Keep windows, baseline vs current ───────────────────────────────
print("Stage 2: Keep-window derivation...")
baseline_windows = active_windows_from_contacts(contacts, duration, **BASELINE)
grouped = active_windows_from_contacts(contacts, duration, **overrides)

if args.no_bridge:
    windows = grouped
else:
    positions = [{"time": p.time, "x": p.x, "y": p.y} for p in tracker.positions]
    windows = bridge_windows_by_motion(grouped, positions, **bridge_overrides)
    print("  Motion-bridge decisions per gap:")
    for (s1, e1), (s2, e2) in zip(grouped, grouped[1:]):
        merged = not any(abs(w_s - s2) < 1e-6 for w_s, _ in windows)
        print(f"    {e1:>7.1f} – {s2:<7.1f} ({s2 - e1:>5.1f}s)  "
              f"{'BRIDGED (kept as play)' if merged else 'cut (stays dead time)'}")
    print()

label = f"overrides: {overrides}" if overrides else "current code defaults"
if not args.no_bridge:
    label += f" + motion bridge {bridge_overrides or '(defaults)'}"
baseline_kept = print_windows("CF-24 baseline", baseline_windows, duration)
kept = print_windows(label, windows, duration)

# ── Stage 3: Diff — footage the baseline cut but we now keep ─────────────────
print("Stage 3: Newly-kept segments (baseline cut these — eyeball them):")
newly_kept = subtract_intervals(windows, baseline_windows)
if newly_kept:
    for s, e in newly_kept:
        print(f"    {s:>8.1f} – {e:>8.1f}  ({e - s:.1f}s)")
    print(f"  {len(newly_kept)} segments, {sum(e - s for s, e in newly_kept):.1f}s "
          f"regained ({kept - baseline_kept:+.1f}s net vs baseline)\n")
else:
    print("    none — identical coverage to baseline\n")

# ── Stage 4: Stitch ──────────────────────────────────────────────────────────
if not have_video:
    print("Stage 4: skipped (no video file — cache-only run)")
    sys.exit(0)

print("Stage 4: Condensed video...")
OUTPUT_DIR.mkdir(exist_ok=True)
condensed_path, condensed_duration = generate_condensed_video(
    str(video_path), windows, OUTPUT_DIR,
)
print(f"  {condensed_path}  ({condensed_duration:.1f}s)")
print(f"\n  Original {duration/60:.1f} min → condensed {condensed_duration/60:.1f} min")
