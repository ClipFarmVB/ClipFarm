"""
Threshold sweep for CF-103 (#116), driven off a dumped ball track.

Replays the whole chain — find_contacts -> active_windows_from_contacts ->
bridge_windows_by_motion -> evaluate_deadtime — against the dead-time fixture,
so a candidate threshold costs a few seconds and no video download. Requires
only the dump from diagnose_detection.py, which the container mounts.

Step 0 prints the sweep's own baseline row, and beneath it the last recorded
run for that fixture with the tag and commit it came from. That is context, not
a pass/fail check: nothing here verifies the recorded run describes the
configuration you are on. Restoring step 0 to a trustworthy control is CF-416
(#547), and it needs more than a fresh row. The newest recorded run is the
`rules` figure (ml/eval/README.md's CF-187 table: 56.2% dead, 176s live), its
`app.config` snapshot carries no `condense_mode` at all, and this tool replays
`active_windows_from_contacts` + `bridge_windows_by_motion` — the `rules` path
— while `condense_mode` has shipped `guarded` since CF-187. So a row
re-recorded at app defaults would match step 0 LESS, not more. Either this tool
scores the shipping builder, or "shipping defaults" here means
the tuner's and the table says so. Re-recording also needs the R2 ball caches
(#511).

  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts
  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts test2

`--exponents` (CF-376, #476) prints the soft-scale axis instead of the sweep:
switch off, switch on, and on + soft at each exponent in EXPONENT_AXIS, with
rally counts and dead-time numbers for both condense builders. It reads the
dump if there is one and the fixture's R2 ball cache otherwise (`load_track`),
so it also runs on test1.

  docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts test2 --exponents

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
"CONTACT_HIT_SPEED_PXPS=360" is applying 1080. main() prints the active scale.
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
# CF-376's soft flag (app.config `ball_contact_soft_scale_enabled`), mirrored for
# the same reason and held to the app default the same way. It reaches
# find_contacts only: production gives the bridge the full scale, so this does
# too. Read only while NORMALIZE is on — off, the scale is already 1.0.
SOFT_SCALE: bool = False

TUNABLES = (
    "CONTACT_RESIDUAL_RATIO", "CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_HIT_SPEED_PXPS",
    "MIN_CONTACT_SPACING", "SEG_MIN_POSITIONS",
    "SEG_MIN_MEDIAN_SPEED_PXPS", "SEG_MAX_SPEED_PXPS", "MAX_SAMPLE_GAP_SEC",
)

# The swept values per knob, and the combined rows, as data rather than as
# literals inside main(). Hoisted for CF-309: a value equal to the shipping
# default re-scores the baseline under another name, and `test_tune_contacts_sweep.py`
# can only assert that if it can read the values.
#
# This drifts silently and has: the tuple below held 240.0 from the days when
# `ball.CONTACT_RESIDUAL_MIN_PXPS` was 480, and CF-103 moving the default to
# 240 turned that row into a second copy of the baseline without touching this
# file. The test now fails instead of the table quietly repeating itself.
SWEEPS: dict[str, tuple[float | int, ...]] = {
    "CONTACT_RESIDUAL_MIN_PXPS": (360.0, 180.0, 120.0),
    "CONTACT_RESIDUAL_RATIO": (0.35, 0.25, 0.15),
    "CONTACT_HIT_SPEED_PXPS": (180.0, 120.0, 90.0),
    "SEG_MIN_POSITIONS": (3, 2),
    "SEG_MIN_MEDIAN_SPEED_PXPS": (40.0, 20.0, 0.0),
    "MIN_CONTACT_SPACING": (0.4, 0.3),
}

# The combined rows, each a full override set. `CONTACT_RESIDUAL_MIN_PXPS=240`
# used to be pinned in every one of these; it is the shipping default now, so
# `score()` applied it as a no-op and "combo: resid 240 + hit 120" was an
# alias for the CONTACT_HIT_SPEED_PXPS=120 row already printed above it. Both
# the pin and that row are gone — the remaining two are genuine combinations.
#
# Dict *literals*, not `dict(...)` calls. The test imports these tables, so a
# call would run fine — but it also compares the literal it reads here against
# what the module ends up bound to, and `ast.literal_eval` cannot evaluate a
# call node. Writing the values out keeps the file readable as the table it is.
COMBOS: tuple[tuple[str, dict[str, float | int]], ...] = (
    ("combo: hit 120 + ratio 0.25",
     {"CONTACT_HIT_SPEED_PXPS": 120.0, "CONTACT_RESIDUAL_RATIO": 0.25}),
    ("combo: + seg 3/40",
     {"CONTACT_HIT_SPEED_PXPS": 120.0, "CONTACT_RESIDUAL_RATIO": 0.25,
      "SEG_MIN_POSITIONS": 3, "SEG_MIN_MEDIAN_SPEED_PXPS": 40.0}),
)


# Stage 2's padding rows, hoisted for the same reason as the two tables above:
# the guard compares every row the tuner prints against the tables, and a row
# it cannot account for is the finding. These are `COND` overrides, not ball
# constants, so they live apart from SWEEPS.
#
# Row 0 is the current COND padding, on purpose: it is stage 2's anchor, the
# best combo scored at today's pad/merge, so every row below it reads as a
# change from that. It therefore repeats the last combo row above it — the one
# named exception to "no swept value equals its default", kept because it is
# the reference and not an accidental copy the way `=240` was.
# `test_the_padding_anchor_is_the_current_COND` holds it to COND so the anchor
# follows the defaults instead of drifting.
PADDING: tuple[tuple[float, float, float], ...] = (
    (5.0, 4.0, 5.0), (4.0, 3.0, 3.0), (3.0, 2.0, 3.0),
    (3.0, 2.0, 2.0), (2.0, 1.5, 2.0), (2.0, 1.0, 1.0),
)

BASELINE_LABEL = "BASELINE (shipping defaults)"


def padding_label(pb: float, pa: float, mg: float) -> str:
    """The label for one padding row. Shared so the guard cannot spell it
    differently from the tuner and call the difference a finding. `:g`, as for
    the knob labels, because `:.0f` printed 2.5 and 2.0 both as `2`."""
    return f"pad {pb:g}/{pa:g} merge {mg:g}"


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


def load_track(test_id: str = DEFAULT_FIXTURE):
    """`load`, falling back to the fixture's R2 ball cache when there is no dump.

    test1 has no `_ball_track.json` dump, so `load` fails on it. Its track is in
    `ball_caches/` under the fixture's `source_video_md5`, which is where
    `deadtime_variants.load_game` reads it from; the frame height comes from the
    fixture's `source_frame_height` the same way. A dump, when present, still
    wins, so every fixture that `load` could already read is read unchanged.
    """
    if (RESULTS_DIR / f"{test_id}_ball_track.json").exists():
        return load(test_id)
    # Lazy: deadtime_variants imports this module.
    from ml.eval.deadtime_variants import BALL_CACHE_DIR

    fx = load_deadtime_fixture(test_id)
    md5 = fx.raw.get("source_video_md5")
    cache = BALL_CACHE_DIR / f"{md5}.json"
    if not md5 or not cache.exists():
        raise SystemExit(
            f"{test_id}: no {test_id}_ball_track.json dump and no ball cache "
            f"{cache.name} (see ml/eval/README.md)"
        )
    raw = json.loads(cache.read_text(encoding="utf-8"))["positions"]
    track = B.TrackedBall(positions=[B.BallPosition(**p) for p in raw])
    positions = [{"time": p["time"], "x": p["x"], "y": p["y"]} for p in raw]
    return track, positions, int(fx.raw.get("source_frame_height", 1080)), fx


def gate_scale(frame_h: int, *, normalize: bool, soft_scale: bool,
               exponent: float | None = None) -> float:
    """The scale find_contacts actually gates on, for a table header.

    `_scale_for` alone is the full CF-174 scale, which is not the number in
    force once CF-376's soft flag is on. `exponent` defaults to the module's.
    """
    scale = B._scale_for(frame_h, log=False, normalize=normalize)
    if soft_scale and scale != 1.0:
        e = B.CONTACT_SOFT_SCALE_EXPONENT if exponent is None else exponent
        scale = scale ** e
    return scale


# CF-376 (#476): the exponent axis. Each row is one contact setting —
# (label, normalize, soft_scale, exponent) — scored on the rules path this tool
# already replays AND on the guarded path (`deadtime_variants.v5_guarded`, which
# calls the builder production ships). `exponent` None is the module constant.
# Soft at 1.0 is the "switch on" row again by construction (x ** 1.0 == x); it
# is kept as the control that says so.
#
# Kept apart from SWEEPS: these are not ball-constant overrides, and SWEEPS'
# guards (no value equal to the default, a pinned key set) are about those.
EXPONENT_AXIS: tuple[tuple[str, bool, bool, float | None], ...] = (
    ("switch off", False, False, None),
    ("switch on", True, False, None),
    ("on + soft e=1", True, True, 1.0),
    ("on + soft e=0.75", True, True, 0.75),
    ("on + soft e=0.5", True, True, 0.5),
    ("on + soft e=0.25", True, True, 0.25),
)


def score_setting(loaded, *, normalize: bool, soft_scale: bool,
                  exponent: float | None = None,
                  overrides: dict[str, float] | None = None) -> dict[str, Any]:
    """Contacts, rallies, and both condense builders' dead-time numbers for one
    contact setting on one loaded fixture (`load_track`'s return).

    The bridge gets `normalize` and not `soft_scale`, as in production: soft
    contacts joined by a full-scale bridge is the combination the flag ships.
    `overrides` sets ball constants for the duration of the call, as
    `_sweep`'s `score` does, and both are restored on the way out.
    """
    from ml.eval import deadtime_variants as dv

    track, positions, frame_h, fx = loaded
    overrides = dict(overrides or {})
    if exponent is not None:
        overrides["CONTACT_SOFT_SCALE_EXPONENT"] = exponent
    saved = {k: getattr(B, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(B, k, v)
        contacts = B.find_contacts(track, frame_height=frame_h, normalize=normalize,
                                   soft_scale=soft_scale)
    finally:
        for k, v in saved.items():
            setattr(B, k, v)

    rallies = B.contacts_to_rallies(contacts, fx.duration, frame_h)
    rules = active_windows_from_contacts(
        [{"time": c["time"]} for c in contacts], fx.duration, **COND)
    rules = bridge_windows_by_motion(rules, positions, frame_height=frame_h,
                                     normalize=normalize, **BRIDGE)
    game = dv.Game(
        test_id=fx.test_id, video_file="", duration=fx.duration, frame_height=frame_h,
        positions=positions, contacts=contacts, human_keep=sorted(fx.keep), raw=fx.raw,
    )
    guarded = dv.v5_guarded(game)
    out: dict[str, Any] = dict(
        contacts=len(contacts),
        segments=len(B.contact_segments(contacts)),
        rallies=len(rallies),
        scale=gate_scale(frame_h, normalize=normalize, soft_scale=soft_scale,
                         exponent=exponent),
    )
    for name, windows in (("rules", rules), ("guarded", guarded)):
        s = evaluate_deadtime(fx.keep, windows, fx.duration)
        out[name] = dict(dead=s.dead_removed_pct, live=s.live_removed_sec,
                         recall=s.kept_play_pct)
    return out


def _pct(x: float | None) -> str:
    return "     -" if x is None else f"{100 * x:5.1f}%"


def exponent_sweep(test_id: str) -> None:
    """Print EXPONENT_AXIS for one fixture: `tune_contacts <id> --exponents`.

    Rally counts on an unlabelled fixture say how many rallies the detector
    emits, not whether they are real; the dead-time columns are scored against
    the fixture's human in-play spans.
    """
    loaded = load_track(test_id)
    frame_h = loaded[2]
    print(f"{test_id}: frame_height={frame_h}, CF-376 exponent axis "
          "(dead-rm / live-lost / recall per condense builder)\n")
    print("%-18s %6s %5s %5s %5s | %-24s | %-24s" % (
        "setting", "scale", "cont", "segs", "rally",
        "rules  dead  live recall", "guarded dead live recall"))
    for label, normalize, soft, exponent in EXPONENT_AXIS:
        r = score_setting(loaded, normalize=normalize, soft_scale=soft, exponent=exponent)
        cols = []
        for name in ("rules", "guarded"):
            m = r[name]
            cols.append("%s %5.0fs %s" % (_pct(m["dead"]), m["live"], _pct(m["recall"])))
        print("%-18s %6.2f %5d %5d %5d | %-24s | %-24s" % (
            label, r["scale"], r["contacts"], r["segments"], r["rallies"], *cols))


def _row(label: str, r: dict, total_rallies: int) -> str:
    """One results line. The rally denominator comes from the fixture.

    It was `126` — test1's rally count — inlined in the format string, which was
    invisible while test1 was the only fixture this could run on and wrong for
    every other one the moment it could (CF-174, CF-309).
    """
    return "%-34s %5d %5d %4d/%-3d %7.0fs %8.1f%% %8.1f%% %8.1f%%" % (
        label, r["contacts"], r["windows"], r["hit"], total_rallies,
        r["live"], 100 * r["dead"], 100 * r["recall"], 100 * r["cond"])


def _last_recorded_run(test_id: str) -> dict | None:
    """The newest row in the fixture's results file, or None.

    **Deliberately not filtered by whether it describes what is running.** An
    earlier version of this function tried: it compared the row's snapshot of
    the ball constants, then also the condense settings this tool mirrors. It
    lasted two review rounds, and each found more inputs the numbers depend on
    and the predicate missed — `condense_mode` selects a different window builder
    entirely, the bridge knobs are a third set, and an absent snapshot section
    matched vacuously. Deciding "does this row describe today's configuration"
    means enumerating every input to the figures, and getting it wrong produces
    a *wrong* pin, which is worse than none: step 0's rule tells the operator to
    distrust everything below an unmatched baseline.

    So this no longer claims. It reports what was last recorded and what it was
    recorded against, and leaves the comparison to the reader. Making step 0 a
    trustworthy control again is CF-416 (#547), which is open and owns exactly
    that — and what it needs is the module docstring's answer, not a matcher
    bolted on here. Not simply a re-recorded row either: see there for why a row
    taken at app defaults would match step 0 less, not more.
    """
    path = RESULTS_DIR / f"{test_id}_deadtime.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            last = json.loads(line)
        except json.JSONDecodeError:
            continue        # a crashed append leaves a partial last line
    return last


def _baseline_note(test_id: str) -> str:
    """Context for the step-0 row — explicitly not a pass/fail pin."""
    row = _last_recorded_run(test_id)
    if row is None:
        return f"  ^ no recorded run for {test_id}\n"
    d = row.get("deadtime") or {}
    # `is not None`, not `in`: two of these three are *legitimately* null in a
    # well-formed row. metrics.py returns None for dead_removed_pct when the
    # fixture has no human dead time and for kept_play_pct when it has no human
    # keep time, and harness._round passes None through on purpose, so the key
    # is present and the value is not a number. A presence-only guard let that
    # row reach `100 * None`, and this note is printed before the first sweep
    # row -- a malformed record has to degrade the note, not kill the run.
    if not all(d.get(k) is not None
               for k in ("live_removed_sec", "dead_removed_pct", "kept_play_pct")):
        return f"  ^ last recorded run for {test_id} has no dead-time metrics\n"
    return (
        "  ^ last recorded run (%s, %s): %.0fs live-lost, %.1f%% dead-rm, "
        "%.1f%% recall\n"
        "    NOT a pass/fail check — whether that run describes your "
        "configuration is not verified here (#547).\n"
        % (row.get("version_tag", "?"), row.get("git_commit", "?"),
           d["live_removed_sec"], 100 * d["dead_removed_pct"],
           100 * d["kept_play_pct"])
    )


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    # `--exponents` (CF-376) prints the soft-scale axis instead of the sweep.
    exponents = "--exponents" in args
    args = [a for a in args if a != "--exponents"]
    test_id = args[0] if args else DEFAULT_FIXTURE

    # Restored on the way out: main() takes an argument now, so it is callable
    # rather than only a __main__ entry point, and a global logging level it
    # never puts back leaks into whatever called it.
    previous = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        if exponents:
            exponent_sweep(test_id)
        else:
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
            contacts = B.find_contacts(track, frame_height=frame_h, normalize=NORMALIZE,
                                       soft_scale=SOFT_SCALE)
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
    scale = gate_scale(frame_h, normalize=NORMALIZE, soft_scale=SOFT_SCALE)
    units = ("labels below are effective px/s" if scale == 1.0
             else "labels below are REFERENCE px/s — multiply by the scale")
    soft = " (CF-376 soft)" if SOFT_SCALE and scale != 1.0 else ""
    print(f"fixture frame_height={frame_h} -> CF-174 threshold scale {scale:.2f}{soft}"
          f"  ({units})\n")
    print("%-34s %5s %5s %8s %8s %9s %9s %9s" % (
        "config", "cont", "win", "rally", "live-lost", "dead-rm", "recall", "condense"))
    show(BASELINE_LABEL, score())
    print(_baseline_note(test_id))

    # `%g` rather than a per-knob format, now that one table holds a mix of
    # floats and ints. It reproduces the labels the earlier per-knob `%.0f`
    # and bare `{v}` formats printed, byte-for-byte, for every value swept.
    #
    # Byte-exact for these values, not label-stable in general: `%g` truncates
    # to six significant digits and switches to scientific notation at 1e6, so
    # a future swept 0.1234567 would print a label that no longer identifies
    # the value it scored.
    for name in ("CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_RESIDUAL_RATIO",
                 "CONTACT_HIT_SPEED_PXPS"):
        for v in SWEEPS[name]:
            show(f"{name}={v:g}", score(**{name: v}))
        print()
    for name in ("SEG_MIN_POSITIONS", "SEG_MIN_MEDIAN_SPEED_PXPS"):
        for v in SWEEPS[name]:
            show(f"{name}={v:g}", score(**{name: v}))
    print()
    for v in SWEEPS["MIN_CONTACT_SPACING"]:
        show(f"MIN_CONTACT_SPACING={v:g}", score(MIN_CONTACT_SPACING=v))
    print()
    # Most promising single knobs, combined.
    for label, overrides in COMBOS:
        show(label, score(**overrides))

    # Stage 2: recovering the condense ratio. Better contact recall pushes the
    # run up against the padding ceiling (pad 5/4 + merge 5 absorbs every dead
    # gap <= 14s), so re-sweep padding on top of the best contact settings.
    # The last combo, read rather than re-typed. This was a fourth hand-written
    # duplicate of the same override set, and a copy is how the no-op
    # `CONTACT_RESIDUAL_MIN_PXPS=240` pin came to survive in four places.
    # Copied on the way out all the same: `COMBOS[-1][1]` is a module-level
    # dict, and handing it to a function by reference where the old code built
    # a fresh one is a class of bug for the sake of nothing.
    #
    # `[-1]` is positional and load-bearing: stage 2 sweeps padding on top of
    # the FULLEST combo, which is the last one because the table is written
    # cumulatively, so the order is part of the table's meaning rather than its
    # presentation. `test_the_combos_are_written_cumulatively` enforces it,
    # so a reordered table fails that test rather than re-basing stage 2.
    best = dict(COMBOS[-1][1])
    print("\n-- padding sweep, on top of the full best contact combo --")
    global COND
    keep_cond = dict(COND)
    try:
        for pb, pa, mg in PADDING:
            COND = dict(keep_cond, pad_before=pb, pad_after=pa, merge_gap_seconds=mg)
            show(padding_label(pb, pa, mg), score(**best))
    finally:
        # Restored on the failure path too: a raise inside the loop would
        # otherwise leave this module global on the last swept value. `score`
        # already guards the ball constants this way, and `main` the logging
        # level; this was the one sweep still restoring only on success.
        COND = keep_cond


if __name__ == "__main__":
    main()
