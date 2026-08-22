"""
Threshold sweep for CF-103 (#116), driven off a dumped ball track.

Replays the whole chain — find_contacts -> active_windows_from_contacts ->
bridge_windows_by_motion -> evaluate_deadtime — against the dead-time fixture,
so a candidate threshold costs a few seconds and no video download. Requires
only the dump from diagnose_detection.py, which the container mounts.

Step 0 reproduces the recorded container baseline. If that row doesn't match
exactly, nothing below it is trustworthy, so it prints the expected values.

  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts
CF-174 — read the labels as REFERENCE (360p) values, not effective ones. The
three px/s tunables (CONTACT_HIT_SPEED_PXPS, CONTACT_RESIDUAL_MIN_PXPS,
MIN_SPEED_PXPS) are multiplied by ball._scale_for(frame_height) at use, and
SEG_MAX_SPEED_PXPS additionally feeds that function's cap, so a row sweeping it
moves the clamp underneath itself. The default fixture is test1 at 360p, where
the scale is exactly 1.0 and label == effective, which is why the pinned
baseline still reproduces; on the 1080p fixtures a row reading
"CONTACT_HIT_SPEED_PXPS=360" is applying 1080. main() prints the active scale.

  docker compose run --rm --no-deps worker python -m ml.eval.tune_contacts
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ml.eval.harness import RESULTS_DIR, load_deadtime_fixture
from ml.eval.metrics import evaluate_deadtime
from ml.pipeline import ball as B
from ml.pipeline.dead_time import active_windows_from_contacts, bridge_windows_by_motion

# Production condense settings for the rule-based path (condense_mode="rules"),
# as recorded in the baseline result row. Annotated Any because the values are
# mixed — min_contacts is an int, and an inferred dict[str, float] makes every
# **COND call site a type error. test_eval_condense_settings.py holds these to
# the app.config defaults.
COND: dict[str, Any] = dict(gap_seconds=10.0, pad_before=5.0, pad_after=4.0,
                            min_contacts=1, merge_gap_seconds=5.0)
BRIDGE: dict[str, Any] = dict(speed_pxps=150.0, fast_fraction=0.35, max_bridge_seconds=20.0)

TUNABLES = (
    "CONTACT_RESIDUAL_RATIO", "CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_HIT_SPEED_PXPS",
    "MIN_SPEED_PXPS", "MIN_CONTACT_SPACING", "SEG_MIN_POSITIONS",
    "SEG_MIN_MEDIAN_SPEED_PXPS", "SEG_MAX_SPEED_PXPS", "MAX_SAMPLE_GAP_SEC",
)


def load(test_id: str = "test1"):
    d = json.loads((RESULTS_DIR / f"{test_id}_ball_track.json").read_text(encoding="utf-8"))
    fps = d["fps"]
    track = B.TrackedBall(positions=[
        # frame/confidence are output-only in find_contacts; reconstructing
        # frame from time is exact at fixed fps and never feeds the logic.
        B.BallPosition(frame=int(round(p["time"] * fps)), time=p["time"],
                       x=p["x"], y=p["y"], confidence=1.0)
        for p in d["positions"]
    ])
    positions = [{"time": p["time"], "x": p["x"], "y": p["y"]} for p in d["positions"]]
    return track, positions, d["frame_height"], load_deadtime_fixture(test_id)


def main() -> None:
    logging.disable(logging.INFO)
    track, positions, frame_h, fx = load()
    defaults = {k: getattr(B, k) for k in TUNABLES}
    rallies = sorted(fx.keep)

    def score(**overrides):
        for k, v in defaults.items():
            setattr(B, k, overrides.get(k, v))
        try:
            contacts = B.find_contacts(track, frame_height=frame_h)
            w = active_windows_from_contacts(
                [{"time": c["time"]} for c in contacts], fx.duration, **COND)
            w = bridge_windows_by_motion(w, positions, frame_height=frame_h, **BRIDGE)
            s = evaluate_deadtime(fx.keep, w, fx.duration)
            times = sorted(c["time"] for c in contacts)
            hit = i = 0
            for a, b in rallies:
                while i < len(times) and times[i] < a:
                    i += 1
                hit += 1 if (i < len(times) and times[i] <= b) else 0
            return dict(contacts=len(contacts), windows=len(w), hit=hit,
                        live=s.live_removed_sec, dead=s.dead_removed_pct,
                        recall=s.kept_play_pct, cond=s.condense_ratio)
        finally:
            for k, v in defaults.items():
                setattr(B, k, v)

    def show(label, r):
        print("%-34s %5d %5d %4d/126 %7.0fs %8.1f%% %8.1f%% %8.1f%%" % (
            label, r["contacts"], r["windows"], r["hit"],
            r["live"], 100 * r["dead"], 100 * r["recall"], 100 * r["cond"]))

    scale = B._scale_for(frame_h)
    units = ("labels below are effective px/s" if scale == 1.0
             else "labels below are REFERENCE px/s — multiply by the scale")
    print(f"fixture frame_height={frame_h} -> CF-174 threshold scale {scale:.2f}"
          f"  ({units})\n")
    print("%-34s %5s %5s %8s %8s %9s %9s %9s" % (
        "config", "cont", "win", "rally", "live-lost", "dead-rm", "recall", "condense"))
    show("BASELINE (shipping defaults)", score())
    print("  ^ expect 214 contacts, 517s live-lost, 68.4% dead-rm, 58.4% recall\n")

    for v in (360.0, 240.0, 180.0, 120.0):
        show(f"CONTACT_RESIDUAL_MIN_PXPS={v:.0f}", score(CONTACT_RESIDUAL_MIN_PXPS=v))
    print()
    for v in (0.35, 0.25, 0.15):
        show(f"CONTACT_RESIDUAL_RATIO={v}", score(CONTACT_RESIDUAL_RATIO=v))
    print()
    for v in (180.0, 120.0, 90.0):
        show(f"CONTACT_HIT_SPEED_PXPS={v:.0f}", score(CONTACT_HIT_SPEED_PXPS=v))
    print()
    for v in (3, 2):
        show(f"SEG_MIN_POSITIONS={v}", score(SEG_MIN_POSITIONS=v))
    for v in (40.0, 20.0, 0.0):
        show(f"SEG_MIN_MEDIAN_SPEED_PXPS={v:.0f}", score(SEG_MIN_MEDIAN_SPEED_PXPS=v))
    print()
    for v in (0.4, 0.3):
        show(f"MIN_CONTACT_SPACING={v}", score(MIN_CONTACT_SPACING=v))
    print()
    # Most promising single knobs, combined.
    show("combo: resid 240 + hit 120",
         score(CONTACT_RESIDUAL_MIN_PXPS=240.0, CONTACT_HIT_SPEED_PXPS=120.0))
    show("combo: + ratio 0.25",
         score(CONTACT_RESIDUAL_MIN_PXPS=240.0, CONTACT_HIT_SPEED_PXPS=120.0,
               CONTACT_RESIDUAL_RATIO=0.25))
    show("combo: + seg 3/40",
         score(CONTACT_RESIDUAL_MIN_PXPS=240.0, CONTACT_HIT_SPEED_PXPS=120.0,
               CONTACT_RESIDUAL_RATIO=0.25, SEG_MIN_POSITIONS=3,
               SEG_MIN_MEDIAN_SPEED_PXPS=40.0))

    # Stage 2: recovering the condense ratio. Better contact recall pushes the
    # run up against the padding ceiling (pad 5/4 + merge 5 absorbs every dead
    # gap <= 14s), so re-sweep padding on top of the best contact settings.
    best = dict(CONTACT_RESIDUAL_MIN_PXPS=240.0, CONTACT_HIT_SPEED_PXPS=120.0,
                CONTACT_RESIDUAL_RATIO=0.25, SEG_MIN_POSITIONS=3,
                SEG_MIN_MEDIAN_SPEED_PXPS=40.0)
    print("\n-- padding sweep, on top of the full best contact combo --")
    global COND
    keep_cond = dict(COND)
    for pb, pa, mg in ((5.0, 4.0, 5.0), (4.0, 3.0, 3.0), (3.0, 2.0, 3.0),
                       (3.0, 2.0, 2.0), (2.0, 1.5, 2.0), (2.0, 1.0, 1.0)):
        COND = dict(keep_cond, pad_before=pb, pad_after=pa, merge_gap_seconds=mg)
        show(f"pad {pb:.0f}/{pa:.1f} merge {mg:.0f}", score(**best))
    COND = keep_cond


if __name__ == "__main__":
    main()
