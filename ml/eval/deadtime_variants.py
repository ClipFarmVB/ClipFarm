"""
Candidate keep-window builders for the dead-time condense stage (CF-173 follow-up).

The two endpoints of this ladder ship: `v0` is `condense_mode="rules"` and `v5` is
`condense_mode="guarded"`, the default since CF-187. Both call
`ml/pipeline/dead_time.py` rather than reimplementing it — a variant that scores
well here and drifts from what production runs would be worse than no comparison
at all. `v1`-`v4` stay prototypes: each is one step of the ladder, kept so the
next change can be judged against the rungs rather than only against the ends.

Each builder takes a Game and returns keep-windows, so they are directly
comparable. The diagnosis they respond to (measured on test2 + test4):

  - the motion bridge is net-negative on both games
  - 24% (test2) / 48% (test4) of contacts fire during dead time, and killing
    those is worth more than every parameter change combined
  - 19 of test4's 46 rallies produce no contact at all, and contacts are the
    only thing that can open a window — so that play is cut outright
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ml.eval.harness import load_deadtime_fixture
from ml.eval.tune_contacts import BRIDGE, COND
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
    positions: list[dict]       # {"time", "x", "y"} ball-track samples
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
    return Game(
        test_id=test_id,
        video_file=fx.raw.get("source_video_file", f"{test_id}.mp4"),
        duration=fx.duration,
        frame_height=frame_h,
        positions=[{"time": p["time"], "x": p["x"], "y": p["y"]} for p in positions],
        contacts=find_contacts(tracker, frame_height=frame_h),
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
    """
    return bridge_windows_by_motion(
        _from_contacts(game, game.contacts),
        game.positions,
        speed_pxps=BRIDGE["speed_pxps"],
        fast_fraction=BRIDGE["fast_fraction"],
        max_bridge_seconds=BRIDGE["max_bridge_seconds"],
        frame_height=game.frame_height,
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


Builder = Callable[[Game], list[Interval]]

VARIANTS: dict[str, tuple[str, Builder]] = {
    "v0": ("mode=rules (contacts + motion bridge)", v0_shipped),
    "v1": ("no motion bridge", v1_no_bridge),
    "v2": ("v1 + speed-gated contacts", v2_speed_gated),
    "v3": ("v2 + motion anchor  [harm-first]", v3_anchored),
    "v4": ("v3 + stricter gate, tight pads  [aggressive]", v4_tight_pads),
    "v5": ("mode=guarded — SHIPPING DEFAULT", v5_guarded),
}
