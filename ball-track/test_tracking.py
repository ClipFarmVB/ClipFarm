"""Quick end-to-end test of ml/pipeline/ball.py on a local test clip."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from ml.pipeline.ball import track_ball, find_contacts

VIDEO = "test_spike.mp4"
API_KEY = os.getenv("ROBOFLOW_API_KEY")

print(f"\nTracking ball in {VIDEO}...")
tracker = track_ball(VIDEO, API_KEY)

print(f"\n--- Trajectory ({len(tracker.positions)} positions) ---")
for p in tracker.positions:
    print(f"  t={p.time:.2f}s  frame={p.frame:4d}  pos=({p.x:.0f}, {p.y:.0f})  conf={p.confidence:.2f}")

print(f"\n--- Contacts ---")
contacts = find_contacts(tracker)
if contacts:
    for c in contacts:
        print(f"  t={c['time']:.2f}s  pos=({c['x']:.0f}, {c['y']:.0f})  "
              f"angle={c['angle_change']}deg  speed_change={c['speed_change']:.2f}")
else:
    print("  No contacts detected")
