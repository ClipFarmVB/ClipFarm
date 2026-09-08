"""
Threshold sweep for CF-103 (#116), driven off a dumped ball track.

Replays the whole chain — find_contacts -> active_windows_from_contacts ->
bridge_windows_by_motion -> evaluate_deadtime — against the dead-time fixture,
so a candidate threshold costs a few seconds and no video download. Requires
only the dump from diagnose_detection.py, which the container mounts.

Step 0 reproduces the newest recorded row whose constants still match the
shipping ones, and prints its values. If step 0 doesn't match, nothing below it
is trustworthy. The expectation is read from that row rather than written down,
so a constant moving under it retires the pin instead of falsifying it (CF-309).

  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts
  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts test2

An optional fixture id selects which dump to sweep; it defaults to test1. Each
needs its own `{test_id}_ball_track.json` from diagnose_detection.py — the
tuner reads a dump, never a video, so a fixture with no dump mounted fails at
load rather than silently sweeping the wrong one.

CF-174 — read the labels as REFERENCE (360p) values, not effective ones. The
two px/s tunables (CONTACT_HIT_SPEED_PXPS, CONTACT_RESIDUAL_MIN_PXPS) are
multiplied by ball._scale_for(frame_height) at use, and
SEG_MAX_SPEED_PXPS additionally feeds that function's cap, so a row sweeping it
moves the clamp underneath itself. The default fixture is test1 at 360p, where
the scale is exactly 1.0 and label == effective, so a row's label is the number
actually applied; on the 1080p fixtures a row reading
"CONTACT_HIT_SPEED_PXPS=360" is applying 1080. main() prints the active scale,
and the pinned baseline expectation is printed only for the fixture it was
recorded against.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from ml.eval.harness import RESULTS_DIR, load_deadtime_fixture
from ml.eval.metrics import evaluate_deadtime
from ml.pipeline import ball as B
from ml.pipeline.dead_time import active_windows_from_contacts, bridge_windows_by_motion

DEFAULT_FIXTURE = "test1"

# Production condense settings for the rule-based path (condense_mode="rules"),
# as recorded in the baseline result row. Annotated Any because the values are
# mixed — min_contacts is an int, and an inferred dict[str, float] makes every
# **COND call site a type error. test_eval_condense_settings.py holds these to
# the app.config defaults.
COND: dict[str, Any] = dict(gap_seconds=10.0, pad_before=5.0, pad_after=4.0,
                            min_contacts=1, merge_gap_seconds=5.0)
BRIDGE: dict[str, Any] = dict(speed_pxps=150.0, fast_fraction=0.35, max_bridge_seconds=20.0)
# The CF-174 kill switch (app.config `ball_contact_scale_enabled`), mirrored the
# same way and for the same reason as COND/BRIDGE: this tool runs on a dumped
# track with no app installed, so it cannot read `settings`. It reaches both
# find_contacts and the motion bridge, exactly as production wires it — a sweep
# scored with the two halves on different sides of the switch would describe a
# configuration nothing runs. Held to the app default by
# test_eval_condense_settings.py.
NORMALIZE: bool = True

TUNABLES = (
    "CONTACT_RESIDUAL_RATIO", "CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_HIT_SPEED_PXPS",
    "MIN_CONTACT_SPACING", "SEG_MIN_POSITIONS",
    "SEG_MIN_MEDIAN_SPEED_PXPS", "SEG_MAX_SPEED_PXPS", "MAX_SAMPLE_GAP_SEC",
)


def load(test_id: str = DEFAULT_FIXTURE):
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


def _row(label: str, r: dict, total_rallies: int) -> str:
    """One results line. The rally denominator comes from the fixture.

    It was `126` — test1's rally count — inlined in the format string, which was
    invisible while test1 was the only fixture this could run on and wrong for
    every other one the moment it could (CF-174, CF-309).
    """
    return "%-34s %5d %5d %4d/%-3d %7.0fs %8.1f%% %8.1f%% %8.1f%%" % (
        label, r["contacts"], r["windows"], r["hit"], total_rallies,
        r["live"], 100 * r["dead"], 100 * r["recall"], 100 * r["cond"])


def _recorded_baseline(test_id: str) -> dict | None:
    """The newest recorded row whose constants still match the shipping ones.

    Read, never hardcoded. The literal it replaces — "expect 214 contacts, 517s
    live-lost, 68.4% dead-rm, 58.4% recall" — is the `baseline` row, recorded
    when `ball.py` shipped `CONTACT_RESIDUAL_MIN_PXPS = 480`; CF-103 lowered it
    to 240 and the literal was never re-taken, so step 0 could not match and the
    tool declared its own output untrustworthy on every run. That is CF-309
    (#359), and reading the row is the fix that card asks for, because it cannot
    drift again: a row stops being the pin the moment a constant moves under it.

    A key in the snapshot that no longer exists on the module was deleted since
    the row was written, and is skipped rather than counted as a mismatch —
    otherwise the first deletion would make every recorded row stale forever.
    """
    path = RESULTS_DIR / f"{test_id}_deadtime.jsonl"
    if not path.exists():
        return None
    match = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        snap = row.get("config_snapshot", {}).get("ml.pipeline.ball", {})
        if all(getattr(B, k) == v for k, v in snap.items() if hasattr(B, k)):
            match = row
    return match


def _baseline_note(test_id: str) -> str:
    """What the step-0 row can be compared against, if anything."""
    row = _recorded_baseline(test_id)
    if row is None:
        return (f"  ^ no recorded row for {test_id} matches the shipping "
                f"constants; the row above is not pinned\n")
    d = row["deadtime"]
    return (
        "  ^ expect %.0fs live-lost, %.1f%% dead-rm, %.1f%% recall  "
        "(recorded %s, %s)\n"
        % (d["live_removed_sec"], 100 * d["dead_removed_pct"],
           100 * d["kept_play_pct"], row.get("version_tag", "?"),
           row.get("git_commit", "?"))
    )


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    test_id = args[0] if args else DEFAULT_FIXTURE

    # Restored on the way out: main() takes an argument now, so it is callable
    # rather than only a __main__ entry point, and a global logging level it
    # never puts back leaks into whatever called it.
    previous = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        _sweep(test_id)
    finally:
        logging.disable(previous)


def _sweep(test_id: str) -> None:
    track, positions, frame_h, fx = load(test_id)
    defaults = {k: getattr(B, k) for k in TUNABLES}
    rallies = sorted(fx.keep)

    def score(**overrides):
        for k, v in defaults.items():
            setattr(B, k, overrides.get(k, v))
        try:
            contacts = B.find_contacts(track, frame_height=frame_h, normalize=NORMALIZE)
            w = active_windows_from_contacts(
                [{"time": c["time"]} for c in contacts], fx.duration, **COND)
            w = bridge_windows_by_motion(w, positions, frame_height=frame_h,
                                         normalize=NORMALIZE, **BRIDGE)
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
        print(_row(label, r, len(rallies)))

    # log=False: this is the per-run *label* for the table below, not a second
    # opinion on the video. Left logging on, it reprints _scale_for's multi-line
    # 1080p warning — which find_contacts already emits on every scored row —
    # into the middle of the results table, on exactly the fixtures this tool was
    # extended to cover. classify_contact_action passes log=False for the same
    # reason.
    scale = B._scale_for(frame_h, log=False, normalize=NORMALIZE)
    units = ("labels below are effective px/s" if scale == 1.0
             else "labels below are REFERENCE px/s — multiply by the scale")
    print(f"fixture frame_height={frame_h} -> CF-174 threshold scale {scale:.2f}"
          f"  ({units})\n")
    print("%-34s %5s %5s %8s %8s %9s %9s %9s" % (
        "config", "cont", "win", "rally", "live-lost", "dead-rm", "recall", "condense"))
    show("BASELINE (shipping defaults)", score())
    print(_baseline_note(test_id))

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
