"""Quick end-to-end test of ml/pipeline/ball.py on a local test clip."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from ml.pipeline.ball import track_ball, find_contacts, contacts_to_rallies
import cv2

VIDEO = "test_spike.mp4"
API_KEY = os.getenv("ROBOFLOW_API_KEY")

# Get video metadata
cap = cv2.VideoCapture(VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
video_duration = total_frames / fps
cap.release()

print(f"\nTracking ball in {VIDEO} ({video_duration:.1f}s)...")
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

print(f"\n--- Rally clips ---")
rallies = contacts_to_rallies(contacts, video_duration, frame_height)
if rallies:
    for r in rallies:
        duration = r['end'] - r['start']
        print(f"  {r['start']:.2f}s -> {r['end']:.2f}s  ({duration:.1f}s)")
else:
    print("  No rallies detected")
