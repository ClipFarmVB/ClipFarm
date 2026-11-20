"""
Paired comparison of two keep-window builders on the same games (CF-604).

The research plan's standard protocol (docs/research/highlight-deadtime-plan.md
§4), decided before looking:

- **Primary: McNemar on whole-rally deletions.** Each rally is a pair — deleted
  or not by builder A, deleted or not by builder B — and only the discordant
  pairs carry information. `mcnemar_exact` is the exact two-sided binomial test
  on them.
- **Secondary, descriptive only: summed Δnet and Δlive with a within-game block
  bootstrap** over (rally + following gap) units (`rally_match.units`).
  Resampling within each game ignores the variance between games, so its
  interval says how much the total moves with which rallies a game happened to
  contain, not whether the result generalises to a new gym.

numpy and the standard library only: CI has numpy 1.26.4 and nothing else.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from ml.eval.metrics import intersect, intersect_length, total_length
from ml.eval.visualize_deadtime import LIVE_CUT_COST
from ml.pipeline.intervals import Interval

BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 604


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p on `b` and `c` discordant pairs.

    Under the null each discordant pair goes either way with probability 1/2, so
    min(b, c) is Binomial(b + c, 1/2); the two-sided p doubles its lower tail,
    capped at 1. No discordant pairs is no evidence: p = 1.
    """
    if b < 0 or c < 0:
        raise ValueError(f"counts must be non-negative, got {b}, {c}")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2.0 * tail)


def unit_scores(human_keep: list[Interval], windows: list[Interval],
                spans: Sequence[Interval]) -> np.ndarray:
    """(net_sec, live_cut_sec) of `windows` inside each of `spans`.

    The same quantities as `evaluate_deadtime` + `net_seconds`, restricted to
    one span: dead removed is the span's seconds neither labelled nor kept,
    live cut is its labelled seconds not kept. Over spans that tile the video
    the rows sum to the game's own net and live cut.
    """
    out = np.zeros((len(spans), 2), dtype=np.float64)
    for i, span in enumerate(spans):
        hk = intersect(human_keep, [span])
        mk = intersect(windows, [span])
        live, kept, live_kept = total_length(hk), total_length(mk), intersect_length(hk, mk)
        live_cut = live - live_kept
        dead_removed = (span[1] - span[0]) - (live + kept - live_kept)
        out[i] = (dead_removed - LIVE_CUT_COST * live_cut, live_cut)
    return out


def block_bootstrap(per_game: Sequence[np.ndarray], draws: int = BOOTSTRAP_DRAWS,
                    seed: int = BOOTSTRAP_SEED) -> np.ndarray:
    """Bootstrap totals of per-unit values, resampled within each game.

    `per_game` holds one (units, k) array per game, every one with the same k.
    Each draw resamples every game's units with replacement, as many as it has,
    and sums over all games: the result is (draws, k). Seeded, so a run is
    reproducible. A game with no units contributes 0.
    """
    if not per_game:
        raise ValueError("block_bootstrap needs at least one game")
    rng = np.random.default_rng(seed)
    total = np.zeros((draws, per_game[0].shape[1]), dtype=np.float64)
    for arr in per_game:
        n = arr.shape[0]
        if n == 0:
            continue
        idx = rng.integers(0, n, size=(draws, n))
        total += arr[idx].sum(axis=1)
    return total


def percentile_interval(samples: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    """The central `level` percentile interval of a 1-D sample."""
    lo = (1.0 - level) / 2.0 * 100.0
    return float(np.percentile(samples, lo)), float(np.percentile(samples, 100.0 - lo))
