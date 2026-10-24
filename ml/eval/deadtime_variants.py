"""
Candidate keep-window builders for the dead-time condense stage (CF-173 follow-up).

The two endpoints of this ladder ship: `v0` is `condense_mode="rules"` and `v5` is
`condense_mode="guarded"`, the default since CF-187. Both call
`ml/pipeline/dead_time.py` rather than reimplementing it — a variant that scores
well here and drifts from what production runs would be worse than no comparison
at all. `v1`-`v4` stay prototypes: each is one step of the ladder, kept so the
next change can be judged against the rungs rather than only against the ends.
`v6` is `condense_mode="learned"` (CF-394), scored in-sample on weights that
are not yet validated — see its docstring before reading its row. `v7` is the
same builder without its sparse-track gate (D-0034), also in-sample, and
reachable from the eval ladder only. The held-out numbers for both are
`ml/eval/validate_learned.py` (CF-395).

Each builder takes a Game and returns keep-windows, so they are directly
comparable. The diagnosis they respond to (measured on test2 + test4):

  - the motion bridge is net-negative on both games
  - 24% (test2) / 48% (test4) of contacts fire during dead time, and killing
    those is worth more than every parameter change combined
  - 19 of test4's 46 rallies produce no contact at all, and contacts are the
    only thing that can open a window — so that play is cut outright
"""
from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ml.eval.harness import load_deadtime_fixture
from ml.eval.tune_contacts import (
    BRIDGE,
    COND,
    NORMALIZE,
    SOFT_SCALE,
    STATIC_SITE_FILTER,
    apply_static_site_filter,
)
from ml.pipeline.ball import BallPosition, TrackedBall, find_contacts
from ml.pipeline.dead_time import (
    Interval,
    SpeedSamples,
    active_windows_from_contacts,
    active_windows_guarded,
    bridge_windows_by_motion,
    merge_intervals,
    motion_anchor_windows,
    speed_gate_contacts,
    speed_samples,
)
from ml.pipeline import dead_time_ml
from ml.pipeline.dead_time_ml import (
    TrackTooSparseError,
    active_windows_learned,
    load_weights,
)

BALL_CACHE_DIR = Path(__file__).resolve().parent / "ball_caches"

# The v2/v3 operating point, kept here because only v4/v5 ship: the gentler
# thresholds those rungs were built on. Speeds are frame-heights/s, as in
# dead_time.py. Production's values are condense_guard_gate_speed /
# condense_guard_anchor_speed (0.25 / 0.30).
CONTACT_MIN_SPEED_FH = 0.15
ANCHOR_SPEED_FH = 0.20


@dataclass
class Game:
    test_id: str
    video_file: str
    duration: float
    frame_height: int
    positions: list[dict]       # {"time", "x", "y", "confidence"} ball-track samples
    contacts: list[dict]        # find_contacts() output
    human_keep: list[Interval]
    raw: dict

    @property
    def speed_samples(self) -> SpeedSamples:
        """(times, speeds in frame-heights/s) between consecutive track samples."""
        return speed_samples(self.positions, self.frame_height)


def load_game(test_id: str) -> Game:
    fx = load_deadtime_fixture(test_id)
    md5 = fx.raw["source_video_md5"]
    frame_h = fx.raw.get("source_frame_height", 1080)
    cache = BALL_CACHE_DIR / f"{md5}.json"
    if not cache.exists():
        raise SystemExit(f"{test_id}: ball cache {cache} missing (see ml/eval/README.md)")
    positions = json.loads(cache.read_text())["positions"]
    tracker = TrackedBall(positions=[BallPosition(**p) for p in positions])
    # CF-229: this module's own binding of the mirror, as NORMALIZE is, so
    # visualize_deadtime --static-site-filter can flip it for one run.
    tracker, dicts = apply_static_site_filter(
        tracker,
        [{"time": p["time"], "x": p["x"], "y": p["y"], "confidence": p["confidence"]}
         for p in positions],
        frame_h, enabled=STATIC_SITE_FILTER,
    )
    return Game(
        test_id=test_id,
        video_file=fx.raw.get("source_video_file", f"{test_id}.mp4"),
        duration=fx.duration,
        frame_height=frame_h,
        positions=dicts,
        contacts=find_contacts(tracker, frame_height=frame_h, normalize=NORMALIZE,
                               soft_scale=SOFT_SCALE),
        human_keep=sorted(fx.keep),
        raw=fx.raw,
    )


# ── building blocks ────────────────────────────────────────────────────────
# Game-shaped adapters over dead_time.py, so a rung of the ladder and the
# shipping path cannot diverge.

def _gated_contacts(game: Game, min_speed: float = CONTACT_MIN_SPEED_FH) -> list[dict]:
    return speed_gate_contacts(game.contacts, game.speed_samples, min_speed=min_speed)


def _anchors(game: Game, speed: float = ANCHOR_SPEED_FH) -> list[Interval]:
    return motion_anchor_windows(game.speed_samples, game.duration, speed=speed)


def _from_contacts(game: Game, contacts: list[dict], **overrides) -> list[Interval]:
    cond = dict(COND, **overrides)
    return active_windows_from_contacts(
        [{"time": c["time"]} for c in contacts], game.duration, **cond
    )


# ── variants ───────────────────────────────────────────────────────────────

def v0_shipped(game: Game) -> list[Interval]:
    """condense_mode="rules": contacts → padded windows → motion bridge.

    frame_height is passed for the same reason v5 gets it (CF-174): without it
    the bridge applies a 360p threshold to the 1080p fixtures, so v0 over-bridges
    relative to what production does and every v1..v5 comparison is scored
    against a baseline that does not ship.

    NORMALIZE goes with it, and for a sharper version of the same reason. The
    contacts in `game` already honour the mirror (load_game), so passing it here
    is what keeps both halves of this baseline on one side of the switch. Without
    it, turning the mirror off — which is what matching a rolled-back production
    requires — leaves `main`'s contacts joined by a scaled bridge: the third
    combination CF-174 removed from tasks.py, reintroduced in the function every
    other variant is measured against.
    """
    return bridge_windows_by_motion(
        _from_contacts(game, game.contacts),
        game.positions,
        speed_pxps=BRIDGE["speed_pxps"],
        fast_fraction=BRIDGE["fast_fraction"],
        max_bridge_seconds=BRIDGE["max_bridge_seconds"],
        frame_height=game.frame_height,
        normalize=NORMALIZE,
    )


def v1_no_bridge(game: Game) -> list[Interval]:
    """Drop the motion bridge. Net-negative on both fixtures at every setting."""
    return _from_contacts(game, game.contacts)


def v2_speed_gated(game: Game) -> list[Interval]:
    """v1 + reject contacts on a near-stationary track."""
    return _from_contacts(game, _gated_contacts(game))


def v3_anchored(game: Game) -> list[Interval]:
    """v2 + open windows on sustained fast motion, not just on contacts."""
    windows = _from_contacts(game, _gated_contacts(game))
    return merge_intervals(sorted(windows + _anchors(game)), COND["merge_gap_seconds"])


def v4_tight_pads(game: Game) -> list[Interval]:
    """
    v3 with a stricter gate and the pads shrunk — the aggressive operating point.

    pad_before + pad_after + merge_gap is the gap a real break must exceed to
    survive, and the median inter-rally gap on both fixtures is 13-14s against
    a shipped budget of 14s — so half of all true dead gaps are erased by
    arithmetic before detection quality matters at all. Only safe once v2/v3
    have given every rally its own anchor.
    """
    windows = _from_contacts(
        game,
        _gated_contacts(game, min_speed=0.25),
        pad_before=3.0, pad_after=2.0, merge_gap_seconds=3.0,
    )
    anchors = _anchors(game, speed=0.30)
    return merge_intervals(sorted(windows + anchors), 3.0)


def v5_guarded(game: Game) -> list[Interval]:
    """
    v4, but abstain entirely when the ball track is too sparse to trust.

    Every variant is a large net loss on test3 for the same reason: two thirds
    of its rallies leave no trace in the ball signal, so aggressiveness there
    buys dead time by cutting play. Refusing to condense scores 0 — worse than
    the best case, far better than any variant's actual behaviour — and is the
    honest answer when the input signal is missing.

    This calls the shipping builder directly (condense_mode="guarded"), so this
    row is production's behaviour and not a lookalike of it. Its kwargs default
    to the same numbers as the condense_guard_* settings.
    """
    return active_windows_guarded(
        game.contacts, game.positions, game.duration, game.frame_height,
        gap_seconds=COND["gap_seconds"], min_contacts=COND["min_contacts"],
    )


@functools.lru_cache(maxsize=4)
def _learned_weights(path: Path) -> dict:
    """The weights file at `path`, read once per run. Keyed on the path, so a
    caller that points `dead_time_ml.WEIGHTS_PATH` elsewhere reads that file
    rather than a cached copy of the old one."""
    return load_weights(path, warn_unvalidated=False)


def v6_learned(game: Game) -> list[Interval]:
    """
    condense_mode="learned" (CF-394) on the committed weights — **in-sample**.

    The weights were fitted on all five fixtures (their `"trained_on"`), so this
    row scores the model on the games it learned from and says nothing about
    unseen footage. The held-out numbers are the trainer's leave-one-game-out
    folds, scored through this same builder and held to the epic's validation
    bar by `python -m ml.eval.validate_learned` (CF-395).

    `allow_unvalidated=True` is what lets an unvalidated file be scored at all;
    production never passes it. On a track too sparse for the learned builder
    to judge this returns `v5_guarded`, which is what production's fallback
    chain ships for that game.
    """
    return _learned(game)


def v7_learned_ungated(game: Game) -> list[Interval]:
    """
    `v6` without the sparse-track gate (`min_track_rate=0.0`) — **in-sample**,
    and **eval only** (D-0034): production keeps the gate, and no setting
    reaches this rung.

    It exists so the gate's cost can be read on the ladder. On the five
    fixtures it differs from `v6` on test3 alone, the one game below the
    gate. An empty track still falls back to `v5_guarded`: with no samples there
    is nothing to score, gate or not. Its held-out counterpart is
    `validate_learned`'s ungated rung.
    """
    return _learned(game, min_track_rate=0.0)


def _learned(game: Game, **kwargs) -> list[Interval]:
    weights = _learned_weights(dead_time_ml.WEIGHTS_PATH)
    try:
        return active_windows_learned(
            game.contacts, game.positions, game.duration, game.frame_height,
            weights=weights, allow_unvalidated=True, **kwargs,
        )
    except TrackTooSparseError:
        return v5_guarded(game)


Builder = Callable[[Game], list[Interval]]

VARIANTS: dict[str, tuple[str, Builder]] = {
    "v0": ("mode=rules (contacts + motion bridge)", v0_shipped),
    "v1": ("no motion bridge", v1_no_bridge),
    "v2": ("v1 + speed-gated contacts", v2_speed_gated),
    "v3": ("v2 + motion anchor  [harm-first]", v3_anchored),
    "v4": ("v3 + stricter gate, tight pads  [aggressive]", v4_tight_pads),
    "v5": ("mode=guarded — SHIPPING DEFAULT", v5_guarded),
    "v6": ("mode=learned — IN-SAMPLE, unvalidated weights", v6_learned),
    "v7": ("v6, no sparse-track gate — IN-SAMPLE, eval only", v7_learned_ungated),
}
