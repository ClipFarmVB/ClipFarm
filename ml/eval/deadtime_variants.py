"""
Candidate keep-window builders for the dead-time condense stage (CF-173 follow-up).

Prototypes only — nothing here is wired into the pipeline. `ml/pipeline/dead_time.py`
stays the shipping path; these are scored against it by visualize_deadtime.py so a
change can be judged on the fixtures before it moves into the pipeline.

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
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ml.eval.harness import load_deadtime_fixture
from ml.eval.tune_contacts import BRIDGE, COND
from ml.pipeline.ball import BallPosition, TrackedBall, find_contacts
from ml.pipeline.dead_time import (
    Interval,
    active_windows_from_contacts,
    bridge_windows_by_motion,
    merge_intervals,
)

BALL_CACHE_DIR = Path(__file__).resolve().parent / "ball_caches"

# Speeds are carried in frame-heights/s so 360p and 1080p games share thresholds
# (same normalization as dead_time_ml.FAST_SPEED).
MAX_SAMPLE_SPACING = 1.5   # a longer gap is a tracking dropout, not motion

# Displacement faster than this is the tracker hopping between two different
# objects, not one ball flying: measured on both fixtures, samples above it land
# inside labeled play only 33-53% of the time, *worse* than the 0.40-1.10 band
# (58-77%). Treating them as motion is what makes a between-rally stretch of
# spare-ball flicker look like a rally. Matches ball.py's SEG_MAX_SPEED_PXPS
# (1200 px/s = 1.11 frame-heights/s at 1080p), which splits tracks for the same
# reason.
MAX_PLAUSIBLE_SPEED_FH = 1.11

# A contact is only credible if the ball was actually moving around it. Below
# this the "ball" is a spare sitting in a cart and the contact is a tracking
# artifact — the dominant false-positive class on both fixtures.
CONTACT_MIN_SPEED_FH = 0.15
CONTACT_SPEED_HALF_WINDOW = 1.5

# Sustained fast motion opens a window even with no contact, recovering rallies
# the contact detector never sees.
ANCHOR_SPEED_FH = 0.20
ANCHOR_HALF_WINDOW = 3.0
ANCHOR_MIN_FRACTION = 0.4
ANCHOR_PAD = 2.0
ANCHOR_MIN_SECONDS = 2.0
# Below this many speed samples in the window the fast-fraction is noise, not
# evidence — test2 tracks at 1.5 samples/s, so a couple of stray fast samples
# would otherwise open a window over dead time. Same guard as
# bridge_windows_by_motion's min_samples.
ANCHOR_MIN_SAMPLES = 6

# Below this many usable speed samples per second the ball track is too sparse
# to support any judgement about play: test3 tracks at 0.52/s (vs 1.47-2.63 on
# the other three) and 21 of its 32 rallies produce no contact at all, so every
# variant — shipped included — cuts more than half its live play. The density
# also sits under what ANCHOR_MIN_SAMPLES needs, so the motion anchor cannot
# recover those rallies either. Condensing on that signal is worse than not
# condensing, so abstain instead.
MIN_SPEED_SAMPLE_RATE = 1.0


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
    def speed_samples(self) -> tuple[np.ndarray, np.ndarray]:
        """(times, speeds in frame-heights/s) between consecutive track samples."""
        return _speeds(self.positions, self.frame_height)


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


def _speeds(positions: list[dict], frame_height: int) -> tuple[np.ndarray, np.ndarray]:
    pts = sorted((p["time"], p["x"], p["y"]) for p in positions)
    times: list[float] = []
    vals: list[float] = []
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if not 0 < dt <= MAX_SAMPLE_SPACING:
            continue
        v = math.hypot(x1 - x0, y1 - y0) / dt / frame_height
        if v > MAX_PLAUSIBLE_SPEED_FH:
            continue  # track hop — not a measurement of the ball's motion
        times.append((t0 + t1) / 2)
        vals.append(v)
    return np.array(times), np.array(vals)


# ── building blocks ────────────────────────────────────────────────────────

def speed_gate_contacts(
    game: Game,
    *,
    min_speed: float = CONTACT_MIN_SPEED_FH,
    half_window: float = CONTACT_SPEED_HALF_WINDOW,
) -> list[dict]:
    """
    Drop contacts whose surrounding ball motion is too slow to be real play.

    A contact is a bend in the trajectory; on a track locked to a stationary
    spare ball, detector jitter produces bends indistinguishable from hits.
    Requiring motion around the contact separates the two without touching
    find_contacts' own thresholds.
    """
    st, sv = game.speed_samples
    if not len(st):
        return list(game.contacts)
    kept = []
    for c in game.contacts:
        lo, hi = np.searchsorted(st, [c["time"] - half_window, c["time"] + half_window])
        if hi > lo and float(np.median(sv[lo:hi])) >= min_speed:
            kept.append(c)
    return kept


def motion_anchor_windows(
    game: Game,
    *,
    speed: float = ANCHOR_SPEED_FH,
    half_window: float = ANCHOR_HALF_WINDOW,
    min_fraction: float = ANCHOR_MIN_FRACTION,
    pad: float = ANCHOR_PAD,
    min_seconds: float = ANCHOR_MIN_SECONDS,
    min_samples: int = ANCHOR_MIN_SAMPLES,
) -> list[Interval]:
    """
    Windows from sustained fast ball motion, independent of contacts.

    Contacts are the only thing that can *open* a window in the shipping path,
    so a rally the detector misses is cut outright — the single largest source
    of removed live play. Rally flight is fast and sustained; between-rally
    handling is not.
    """
    st, sv = game.speed_samples
    if not len(st):
        return []
    n = max(1, int(math.ceil(game.duration)))
    centers = np.arange(n) + 0.5
    lo = np.searchsorted(st, centers - half_window)
    hi = np.searchsorted(st, centers + half_window)
    cum = np.concatenate(([0.0], np.cumsum((sv >= speed).astype(float))))
    count = (hi - lo).astype(float)
    frac = np.where(count > 0, (cum[hi] - cum[lo]) / np.maximum(count, 1), 0.0)
    on_mask = (frac >= min_fraction) & (count >= min_samples)

    windows: list[Interval] = []
    start: int | None = None
    for i, on in enumerate(on_mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            windows.append((float(start), float(i)))
            start = None
    if start is not None:
        windows.append((float(start), float(n)))

    return [
        (max(0.0, s - pad), min(game.duration, e + pad))
        for s, e in windows
        if e - s >= min_seconds
    ]


def track_is_usable(game: Game, *, min_rate: float = MIN_SPEED_SAMPLE_RATE) -> bool:
    """Whether the ball track is dense enough for any of this to mean anything."""
    st, _ = game.speed_samples
    return game.duration > 0 and len(st) / game.duration >= min_rate


def _from_contacts(game: Game, contacts: list[dict], **overrides) -> list[Interval]:
    cond = dict(COND, **overrides)
    return active_windows_from_contacts(
        [{"time": c["time"]} for c in contacts], game.duration, **cond
    )


# ── variants ───────────────────────────────────────────────────────────────

def v0_shipped(game: Game) -> list[Interval]:
    """Current production path: contacts → padded windows → motion bridge."""
    return bridge_windows_by_motion(
        _from_contacts(game, game.contacts),
        game.positions,
        speed_pxps=BRIDGE["speed_pxps"],
        fast_fraction=BRIDGE["fast_fraction"],
        max_bridge_seconds=BRIDGE["max_bridge_seconds"],
    )


def v1_no_bridge(game: Game) -> list[Interval]:
    """Drop the motion bridge. Net-negative on both fixtures at every setting."""
    return _from_contacts(game, game.contacts)


def v2_speed_gated(game: Game) -> list[Interval]:
    """v1 + reject contacts on a near-stationary track."""
    return _from_contacts(game, speed_gate_contacts(game))


def v3_anchored(game: Game) -> list[Interval]:
    """v2 + open windows on sustained fast motion, not just on contacts."""
    windows = _from_contacts(game, speed_gate_contacts(game))
    return merge_intervals(sorted(windows + motion_anchor_windows(game)), COND["merge_gap_seconds"])


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
        speed_gate_contacts(game, min_speed=0.25),
        pad_before=3.0, pad_after=2.0, merge_gap_seconds=3.0,
    )
    anchors = motion_anchor_windows(game, speed=0.30)
    return merge_intervals(sorted(windows + anchors), 3.0)


def v5_guarded(game: Game) -> list[Interval]:
    """
    v4, but abstain entirely when the ball track is too sparse to trust.

    Every variant is a large net loss on test3 for the same reason: two thirds
    of its rallies leave no trace in the ball signal, so aggressiveness there
    buys dead time by cutting play. Refusing to condense scores 0 — worse than
    the best case, far better than any variant's actual behaviour — and is the
    honest answer when the input signal is missing.
    """
    if not track_is_usable(game):
        return [(0.0, game.duration)]
    return v4_tight_pads(game)


Builder = Callable[[Game], list[Interval]]

VARIANTS: dict[str, tuple[str, Builder]] = {
    "v0": ("shipped (contacts + motion bridge)", v0_shipped),
    "v1": ("no motion bridge", v1_no_bridge),
    "v2": ("v1 + speed-gated contacts", v2_speed_gated),
    "v3": ("v2 + motion anchor  [harm-first]", v3_anchored),
    "v4": ("v3 + stricter gate, tight pads  [aggressive]", v4_tight_pads),
    "v5": ("v4 + abstain on sparse tracks  [guarded]", v5_guarded),
}
