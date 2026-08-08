"""
Multi-fixture threshold sweep for `find_contacts` (CF-103, #116).

Replays the whole condense chain — find_contacts -> active_windows_from_contacts
-> bridge_windows_by_motion -> evaluate_deadtime — against every dead-time
fixture, so a candidate threshold costs a few seconds, no video download and no
container. Ball positions come from the committed-by-hand ball caches in
`ml/eval/ball_caches/` (same files `train_deadtime.py` uses; see
`ml/eval/README.md` for how to populate them).

**Why it sweeps several games.** The first pass of this tool scored one video,
and the #116 write-up said so plainly: tuning five thresholds against a single
hour of one match fits that match's noise. Only `CONTACT_RESIDUAL_MIN_PXPS`
shipped (#118) for exactly that reason. CF-173 added test2/test3/test4, so a
candidate can now be *chosen on some games and scored on a game it never saw* —
which is what `--loo` does, mirroring the leave-one-game-out protocol in
`train_deadtime.py`. A knob that only wins on the game it was fitted to is
overfitting, and this is the tool that shows it.

Fixtures split into two roles:
  --fixtures  tuned on, and each takes a turn as the held-out validation game.
  --guards    scored and printed, never optimized against. test3 is the default
              guard: its gym uses non-Mikasa balls the tracker barely sees
              (CF-171), so it is a *tracking* failure, not a threshold one.
              A knob change that appears to "fix" test3 is admitting noise.

Usage:
  python -m ml.eval.tune_contacts                       # every knob, then LOO
  python -m ml.eval.tune_contacts --knob CONTACT_HIT_SPEED_PXPS \
      --values 240 230 220 210 200                      # fine sweep of one knob
  python -m ml.eval.tune_contacts --candidates          # LOO over the named CANDIDATES
  python -m ml.eval.tune_contacts --fixtures test2 test4 --guards test1 test3
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ml.eval.harness import EVAL_DIR, DeadFixture, load_deadtime_fixture
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime, union
from ml.pipeline import ball as B
from ml.pipeline.dead_time import active_windows_from_contacts, bridge_windows_by_motion

BALL_CACHE_DIR = EVAL_DIR / "ball_caches"

# Production condense settings. These are a hand copy of the app.config defaults
# — this tool runs with no app installed — and ml/tests/test_eval_condense_settings.py
# fails the build if the two drift, so keep them as plain dict(...) literals.
COND = dict(gap_seconds=10.0, pad_before=5.0, pad_after=4.0,
            min_contacts=1, merge_gap_seconds=5.0)
BRIDGE = dict(speed_pxps=150.0, fast_fraction=0.35, max_bridge_seconds=20.0)

# 1s of wrongly cut play costs this many seconds of kept dead time. Matches the
# repo posture (CF-46): keeping some dead time beats cutting play. Shared with
# train_deadtime.py so the two tools rank candidates by the same trade.
LIVE_CUT_COST = 4.0

# Fixtures worth tuning on. test3 is excluded by default — see the module
# docstring; its fixture note records the tracking failure in full.
DEFAULT_FIXTURES = ("test1", "test2", "test4")
DEFAULT_GUARDS = ("test3",)

TUNABLES = (
    "CONTACT_RESIDUAL_RATIO", "CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_HIT_SPEED_PXPS",
    "MIN_SPEED_PXPS", "MIN_CONTACT_SPACING", "SEG_MIN_POSITIONS",
    "SEG_MIN_MEDIAN_SPEED_PXPS", "SEG_MAX_SPEED_PXPS", "MAX_SAMPLE_GAP_SEC",
)

# Values swept when no --knob is given. Ranges bracket the shipping value on both
# sides: a sweep that only loosens can't tell "this knob is too tight" from
# "loosening always helps the recall half of the metric".
SWEEPS: dict[str, tuple] = {
    "CONTACT_HIT_SPEED_PXPS":    (300.0, 240.0, 220.0, 200.0, 180.0, 150.0, 120.0),
    "CONTACT_RESIDUAL_MIN_PXPS": (360.0, 300.0, 240.0, 180.0, 120.0),
    "CONTACT_RESIDUAL_RATIO":    (0.70, 0.50, 0.35, 0.25, 0.15),
    "MIN_SPEED_PXPS":            (240.0, 180.0, 120.0, 60.0),
    "SEG_MIN_POSITIONS":         (5, 4, 3),
    "SEG_MIN_MEDIAN_SPEED_PXPS": (90.0, 60.0, 40.0, 20.0),
    "SEG_MAX_SPEED_PXPS":        (900.0, 1200.0, 1800.0, 2400.0),
    "MIN_CONTACT_SPACING":       (0.8, 0.6, 0.4, 0.3),
    "MAX_SAMPLE_GAP_SEC":        (0.8, 1.0, 1.5, 2.0),
}

# Whole configurations, for the question a single-knob sweep cannot answer:
# does adding a *second* knob still transfer to a game it was not fitted on?
# Each knob sweeps well alone, so the temptation is to stack the winners — and
# stacking is where a three-game fit starts describing three gyms rather than
# volleyball. --candidates runs the same leave-one-game-out over these, so the
# stack has to earn its place on held-out footage.
#
# This is the record of what CF-103 stage 2 considered and rejected: the two
# stacks below beat hit-220 alone by under 1% of total utility while fitting a
# second threshold to three games, and the resid-180 stack also lifts the test3
# guard (5 -> 6 of 32) on footage whose tracking is broken — contacts appearing
# where the ball is barely seen is noise being admitted, not a fix.
#
# Every knob is pinned explicitly, including ones at their shipping value, so
# these rows keep meaning the same configuration when a default moves.
CANDIDATES: dict[str, dict] = {
    "pre-stage2 (hit 240)": {"CONTACT_HIT_SPEED_PXPS": 240.0},
    "hit 220":              {"CONTACT_HIT_SPEED_PXPS": 220.0},
    "hit 240 + resid 180":  {"CONTACT_HIT_SPEED_PXPS": 240.0,
                             "CONTACT_RESIDUAL_MIN_PXPS": 180.0},
    "hit 220 + resid 180":  {"CONTACT_HIT_SPEED_PXPS": 220.0,
                             "CONTACT_RESIDUAL_MIN_PXPS": 180.0},
    "hit 220 + segmed 40":  {"CONTACT_HIT_SPEED_PXPS": 220.0,
                             "SEG_MIN_MEDIAN_SPEED_PXPS": 40.0},
}


# ── fixtures + scoring ─────────────────────────────────────────────────────

def load_ball_cache(fixture: DeadFixture, cache_dir: Path = BALL_CACHE_DIR) -> tuple[list[dict], int]:
    """
    The fixture's ball track and the tracking space it was produced in.

    Shared with train_deadtime.py: both replay the pipeline off these caches, and
    a fixture missing `source_frame_height` would silently score against 360p
    thresholds on 1080p footage (CF-174), so that is an error, not a default.
    """
    frame_height = fixture.raw.get("source_frame_height")
    if not frame_height:
        raise SystemExit(f"{fixture.test_id}: fixture needs source_frame_height (tracking-space px)")
    md5 = fixture.raw["source_video_md5"]
    path = cache_dir / f"{md5}.json"
    if not path.exists():
        raise SystemExit(
            f"{fixture.test_id}: ball cache {path} missing — download "
            f"ball-cache/{md5}-*.json from R2 (see ml/eval/README.md)"
        )
    return json.loads(path.read_text(encoding="utf-8"))["positions"], int(frame_height)


@dataclass
class Game:
    test_id: str
    fixture: DeadFixture
    frame_height: int
    tracker: B.TrackedBall
    positions: list[dict]      # {time, x, y} dicts, as bridge_windows_by_motion wants
    rallies: list[tuple[float, float]]   # merged in-play spans, for the rally-hit count


def load_game(test_id: str, cache_dir: Path = BALL_CACHE_DIR) -> Game:
    fx = load_deadtime_fixture(test_id)
    raw, frame_height = load_ball_cache(fx, cache_dir)
    return Game(
        test_id=test_id,
        fixture=fx,
        frame_height=frame_height,
        tracker=B.TrackedBall(positions=[B.BallPosition(**p) for p in raw]),
        positions=[{"time": p["time"], "x": p["x"], "y": p["y"]} for p in raw],
        rallies=union(fx.keep),
    )


@dataclass
class Result:
    contacts: int
    keep: list[tuple[float, float]]     # the condense keep-windows this config produced
    rallies_hit: int
    rallies_total: int
    signals: DeadTimeSignals

    @property
    def dead_removed_sec(self) -> float:
        return (self.signals.dead_removed_pct or 0.0) * self.signals.human_dead_sec

    @property
    def utility(self) -> float:
        """Dead seconds removed, minus LIVE_CUT_COST per second of play cut."""
        return self.dead_removed_sec - LIVE_CUT_COST * self.signals.live_removed_sec


def utility(signals: DeadTimeSignals) -> float:
    """Same trade as Result.utility, for callers holding only the signals."""
    dead_removed_sec = (signals.dead_removed_pct or 0.0) * signals.human_dead_sec
    return dead_removed_sec - LIVE_CUT_COST * signals.live_removed_sec


def _rallies_hit(rallies: list[tuple[float, float]], contacts: list[dict]) -> int:
    """
    How many labeled rallies contain at least one contact.

    This is the #116 metric: keep-windows are anchored on contacts, so a rally
    with zero contacts is invisible to the condense stage and gets cut whole. It
    localizes a regression the seconds-based signals blur — losing one long rally
    and trimming thirty short ones cost the same live seconds but are very
    different bugs.
    """
    times = sorted(c["time"] for c in contacts)
    hit = i = 0
    for start, end in rallies:
        while i < len(times) and times[i] < start:
            i += 1
        if i < len(times) and times[i] <= end:
            hit += 1
    return hit


def score(game: Game, overrides: dict | None = None) -> Result:
    """Run the condense chain with `overrides` patched over ball.py's constants."""
    overrides = overrides or {}
    unknown = set(overrides) - set(TUNABLES)
    if unknown:
        raise SystemExit(f"not tunable: {sorted(unknown)} (known: {sorted(TUNABLES)})")

    saved = {k: getattr(B, k) for k in TUNABLES}
    for key, value in overrides.items():
        setattr(B, key, value)
    try:
        contacts = B.find_contacts(game.tracker, frame_height=game.frame_height)
        # Spelled out rather than **COND/**BRIDGE: the dicts are untyped literals
        # (test_eval_condense_settings.py parses them), so splatting them loses
        # min_contacts' int-ness.
        windows = active_windows_from_contacts(
            [{"time": c["time"]} for c in contacts], game.fixture.duration,
            gap_seconds=COND["gap_seconds"], pad_before=COND["pad_before"],
            pad_after=COND["pad_after"], min_contacts=int(COND["min_contacts"]),
            merge_gap_seconds=COND["merge_gap_seconds"],
        )
        windows = bridge_windows_by_motion(
            windows, game.positions, frame_height=game.frame_height,
            speed_pxps=BRIDGE["speed_pxps"], fast_fraction=BRIDGE["fast_fraction"],
            max_bridge_seconds=BRIDGE["max_bridge_seconds"],
        )
        return Result(
            contacts=len(contacts),
            keep=windows,
            rallies_hit=_rallies_hit(game.rallies, contacts),
            rallies_total=len(game.rallies),
            signals=evaluate_deadtime(game.fixture.keep, windows, game.fixture.duration),
        )
    finally:
        for key, value in saved.items():
            setattr(B, key, value)


# ── reporting ──────────────────────────────────────────────────────────────

CELL_W = 31


def _cell(r: Result) -> str:
    s = r.signals
    return (f"{r.rallies_hit:3d}/{r.rallies_total:<3d} {s.live_removed_sec:5.0f}s "
            f"{100 * (s.dead_removed_pct or 0.0):5.1f}% {100 * (s.kept_play_pct or 0.0):5.1f}%")


def _header(games: list[Game]) -> str:
    cols = " | ".join(f"{g.test_id:^{CELL_W}s}" for g in games)
    sub = " | ".join(f"{'rally    live   dead%  recall':{CELL_W}s}" for _ in games)
    return f"{'config':38s} | {cols}\n{'':38s} | {sub}"


def _row(label: str, games: list[Game], results: dict[str, Result], tuned: list[Game]) -> str:
    cells = " | ".join(_cell(results[g.test_id]) for g in games)
    total = sum(results[g.test_id].utility for g in tuned)
    return f"{label:38s} | {cells} | U={total:7.0f}"


def sweep_knob(knob: str, values, games: list[Game], tuned: list[Game]) -> dict:
    """Score every value of one knob on every game. Returns {value: {test_id: Result}}."""
    return {v: {g.test_id: score(g, {knob: v}) for g in games} for v in values}


def report_sweep(knob: str, values, table: dict, games: list[Game], tuned: list[Game]) -> None:
    print(f"\n-- {knob} (shipping {getattr(B, knob):g}) --")
    for v in values:
        mark = "  <- shipping" if v == getattr(B, knob) else ""
        print(_row(f"  {knob}={v:g}{mark}", games, table[v], tuned))


def report_marginals(knob: str, values, table: dict, tuned: list[Game]) -> None:
    """
    What each step along the sweep actually buys, summed over the tuned games.

    This is the knee argument #118 used for the residual floor, made explicit:
    once a step stops recovering rallies while still shedding dead time, the
    extra contacts are false positives and the sweep has gone past the knee.
    """
    print(f"\n-- {knob}: marginal cost of each step --")
    print(f"  {'step':>17} {'rallies':>9} {'live saved':>12} {'dead lost':>11}   dead lost per rally")
    for a, b in zip(values, values[1:]):
        gained = sum(table[b][g.test_id].rallies_hit - table[a][g.test_id].rallies_hit for g in tuned)
        saved = sum(table[a][g.test_id].signals.live_removed_sec
                    - table[b][g.test_id].signals.live_removed_sec for g in tuned)
        lost = sum(table[a][g.test_id].dead_removed_sec - table[b][g.test_id].dead_removed_sec
                   for g in tuned)
        per = f"{lost / gained:.0f}s" if gained else "-- (no rally recovered)"
        print(f"  {a:7g} -> {b:<6g} {gained:+9d} {saved:11.0f}s {lost:10.0f}s   {per}")


def report_loo(knob: str, values, table: dict, tuned: list[Game]) -> None:
    """
    Leave-one-game-out: pick the value on the other games, score it on this one.

    Every number printed under "held out" comes from footage the choice never
    saw. With three games this is a weak test — but it is the difference between
    "this value wins on the video it was fitted to" and "this value transfers",
    and #116 shipped one knob instead of five precisely because nothing could
    tell those apart yet.
    """
    print(f"\n-- {knob}: leave-one-game-out (cost {LIVE_CUT_COST:g}:1) --")
    if len(tuned) < 2:
        print("  needs >= 2 tuned fixtures; skipped")
        return
    for held in tuned:
        train = [g for g in tuned if g is not held]
        pick = max(values, key=lambda v: sum(table[v][g.test_id].utility for g in train))
        chosen, shipping = table[pick][held.test_id], table[getattr(B, knob)][held.test_id]
        print(f"  held out {held.test_id:8s} trained pick {knob}={pick:g}")
        print(f"    picked   {_cell(chosen)}   condense {100 * (chosen.signals.condense_ratio or 0.0):5.1f}%")
        print(f"    shipping {_cell(shipping)}   condense {100 * (shipping.signals.condense_ratio or 0.0):5.1f}%")


def dump_windows(games: list[Game], out_dir: Path, config: str | None = None) -> None:
    """
    Write each game's keep-windows in `--windows-json` shape.

    Closes the loop the results log needs. `harness --offline` derives the same
    windows from the R2 ball-cache, but it wants the worker container, the app
    config and a video download; this path replays the identical chain off the
    committed fixtures, so a threshold change can be recorded in
    results/{test_id}_deadtime.jsonl from a laptop. The windows match --offline
    because both call the same find_contacts -> dead_time.py functions with the
    drift-guarded COND/BRIDGE settings above.

    `config` names a CANDIDATES entry instead of the shipping constants, which is
    how a superseded configuration still gets its own row: without it the log
    jumps straight from the last recorded state to the current one and credits a
    single change with everything in between.
    """
    overrides = CANDIDATES[config] if config else {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for game in games:
        result = score(game, overrides)
        path = out_dir / f"{game.test_id}_keep.json"
        path.write_text(json.dumps(
            {"keep": [{"start": round(a, 3), "end": round(b, 3)} for a, b in result.keep]},
            indent=2,
        ) + "\n", encoding="utf-8")
        print(f"{game.test_id}: {result.contacts} contacts -> {len(result.keep)} windows -> {path}")


def report_candidates(games: list[Game], tuned: list[Game]) -> None:
    """Score every named CANDIDATES config, then leave-one-game-out across them."""
    table = {name: {g.test_id: score(g, cfg) for g in games} for name, cfg in CANDIDATES.items()}
    # Computed, not named in CANDIDATES: the shipping row must track ball.py's
    # current constants, so it stays the right comparison after a default moves.
    shipping = {g.test_id: score(g) for g in games}

    print("\n" + _header(games))
    print(_row("  SHIPPING DEFAULTS", games, shipping, tuned))
    for name in CANDIDATES:
        print(_row(f"  {name}", games, table[name], tuned))

    print(f"\n-- candidates: leave-one-game-out (cost {LIVE_CUT_COST:g}:1) --")
    if len(tuned) < 2:
        print("  needs >= 2 tuned fixtures; skipped")
        return
    names = list(CANDIDATES)
    for held in tuned:
        train = [g for g in tuned if g is not held]
        pick = max(names, key=lambda n: sum(table[n][g.test_id].utility for g in train))
        chosen, ship = table[pick][held.test_id], shipping[held.test_id]
        print(f"  held out {held.test_id:8s} trained pick {pick}")
        print(f"    picked   {_cell(chosen)}   condense {100 * (chosen.signals.condense_ratio or 0.0):5.1f}%")
        print(f"    shipping {_cell(ship)}   condense {100 * (ship.signals.condense_ratio or 0.0):5.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--fixtures", nargs="+", default=list(DEFAULT_FIXTURES),
                    help="fixtures to tune and cross-validate on")
    ap.add_argument("--guards", nargs="+", default=list(DEFAULT_GUARDS),
                    help="fixtures scored but never optimized against")
    ap.add_argument("--ball-cache-dir", type=Path, default=BALL_CACHE_DIR)
    ap.add_argument("--knob", choices=sorted(TUNABLES),
                    help="sweep only this knob (default: every knob in SWEEPS)")
    ap.add_argument("--values", nargs="+", type=float,
                    help="values for --knob, replacing the built-in range")
    ap.add_argument("--candidates", action="store_true",
                    help="skip the knob sweeps; cross-validate the named CANDIDATES configs")
    ap.add_argument("--dump-windows", type=Path, metavar="DIR",
                    help="skip the sweeps; write each fixture's shipping-config keep-windows to "
                         "DIR/{test_id}_keep.json, ready for "
                         "`harness --mode deadtime --windows-json` to score and record")
    ap.add_argument("--config", choices=sorted(CANDIDATES),
                    help="with --dump-windows: dump this CANDIDATES config instead of the "
                         "shipping constants")
    args = ap.parse_args()

    if args.config and not args.dump_windows:
        ap.error("--config applies to --dump-windows")

    if args.values and not args.knob:
        ap.error("--values needs --knob")
    if args.candidates and args.knob:
        ap.error("--candidates sweeps whole configs; drop --knob")
    logging.disable(logging.INFO)   # find_contacts logs a line per call; 40 calls of it drowns the tables

    tuned = [load_game(t, args.ball_cache_dir) for t in args.fixtures]
    guards = [load_game(t, args.ball_cache_dir) for t in args.guards]
    games = tuned + guards
    if not tuned:
        ap.error("--fixtures must name at least one fixture")

    print(f"tuned on {[g.test_id for g in tuned]}, guarded by {[g.test_id for g in guards]}")
    for g in games:
        role = "guard" if g in guards else "tuned"
        print(f"  {g.test_id:8s} {role:5s} {g.fixture.duration:7.0f}s  frame_h={g.frame_height:5d}  "
              f"{len(g.positions):5d} ball positions  {len(g.rallies):3d} labeled rallies")

    if args.dump_windows:
        dump_windows(games, args.dump_windows, args.config)
        return

    if args.candidates:
        report_candidates(games, tuned)
        return

    knobs = ({args.knob: tuple(args.values) if args.values else SWEEPS[args.knob]}
             if args.knob else SWEEPS)

    print("\n" + _header(games))
    print(_row("SHIPPING DEFAULTS", games, {g.test_id: score(g) for g in games}, tuned))

    for knob, values in knobs.items():
        # A value the sweep omits leaves report_loo with no shipping row to
        # compare against, so pin it in and keep the range ordered.
        values = tuple(sorted({*values, getattr(B, knob)}, reverse=True))
        table = sweep_knob(knob, values, games, tuned)
        report_sweep(knob, values, table, games, tuned)
        if len(values) > 1:
            report_marginals(knob, values, table, tuned)
            report_loo(knob, values, table, tuned)


if __name__ == "__main__":
    main()
