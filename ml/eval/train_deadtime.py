"""
Leave-one-game-out trainer for the learned in-play classifier (CF-393).

Fits the per-second logistic model in `ml/pipeline/dead_time_ml.py` against the
hand-labeled in-play spans of the five dead-time fixtures and writes
`ml/pipeline/deadtime_ml_weights.json`. Ported from CF-173's branch trainer and
changed where the epic (CF-389) says to:

- **Games load through `deadtime_variants.load_game`**, so this and the condense
  ladder cannot read a cache differently.
- **The baseline is `guarded`** — `deadtime_variants.v5_guarded`, the shipping
  builder — not CF-173's contacts + bridge path.
- **Per-video robust scaling** (`dead_time_ml.robust_scale`) replaces the global
  mean/std, and the rule is recorded in the weights file (`dead_time_ml.SCALING`,
  which `load_weights` and `predict_in_play` check against the code).
- **Two context variants** — the windowed aggregates alone, and those plus
  lagged copies at ±1/±3/±5s. The one with the higher summed held-out net over
  the comparable fixtures ships; both are reported.

Kept from the branch: the numpy `fit_logistic`, leave-one-game-out validation,
a threshold picked on the training games only (summed net over every training
game at `LIVE_CUT_COST`), and shipped weights fit on all games at the median of
the per-fold thresholds.

**The weights are written with `"validated": false`, always.** Committing them
is not enabling them: nothing in the pipeline reads this file (CF-394 is the
builder), and the bar for flipping the flag is CF-395's to apply.

**Headline totals are over `COMPARABLE` (test2/test4/test5) only.** test1 is a
different labeler on 360p footage and test3 is a game the tracker cannot follow
(`EXCLUDED_FROM_TOTALS` in visualize_deadtime.py). Both are still trained on and
still get a fold.

**Byte-identical output** at the same HEAD: no timestamp, floats rounded to 6dp
and the threshold to 2dp, LF line endings. `git_commit` is HEAD's short sha, so
a run at another commit differs in that field and nowhere else unless the code
or the caches changed.

Needs the five ball caches in `ml/eval/ball_caches/{source_video_md5}.json`
(CF-390; gitignored — copy them in). No video, no app deps.

Usage:
  python -m ml.eval.train_deadtime              # LOO report + write the weights
  python -m ml.eval.train_deadtime --no-write   # report only
  python -m ml.eval.train_deadtime --ablation   # also the feature-subset AUC table
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ml.eval import deadtime_variants as dv
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime, union
from ml.eval.visualize_deadtime import COMPARABLE, LIVE_CUT_COST, TEST_IDS, net_seconds
from ml.pipeline.dead_time_ml import (
    CONTEXT_LAGS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    SCALING,
    WEIGHTS_PATH,
    compute_features,
    context_rule,
    design_matrix,
    predict_in_play,
    windows_from_probability,
)
from ml.pipeline.intervals import Interval

POS_WEIGHT = 3.0            # class weight on in-play seconds during the fit
LEARNING_RATE = 0.1
ITERATIONS = 3000
L2 = 1e-3
# 0.35, 0.40, ... 0.90 — built from integers so no float drift reaches the file.
THRESHOLDS: tuple[float, ...] = tuple(round(0.35 + 0.05 * i, 2) for i in range(12))

# Name → lags. Order matters only for ties: the first listed wins, so a tie
# ships the simpler model.
VARIANTS: dict[str, tuple[int, ...]] = {
    "none": (),
    "lags_1_3_5": CONTEXT_LAGS,
}

# The epic's ablation, over the 13 position-track columns. "base" is what the
# rules read — track density, speed, contacts — without the three columns the
# experiment credited with the gap (confidence, mean height, vertical spread).
_NOT_BASE = ("mean_conf_3s", "max_conf_3s", "mean_y_5s", "y_std_5s")
_BASE = tuple(n for n in FEATURE_NAMES if n not in _NOT_BASE)
ABLATION: dict[str, tuple[str, ...]] = {
    "base": _BASE,
    "+ confidence": _BASE + ("mean_conf_3s", "max_conf_3s"),
    "+ mean height": _BASE + ("mean_y_5s",),
    "all": tuple(FEATURE_NAMES),
}
EPIC_AUC = {"base": 0.666, "+ confidence": 0.853, "+ mean height": 0.840, "all": 0.915}


@dataclass
class TrainGame:
    test_id: str
    source_video_md5: str
    duration: float
    human_keep: list[Interval]
    features: np.ndarray        # raw compute_features output, one row per second
    y: np.ndarray               # 1.0 where the second's centre is in-play
    guarded: DeadTimeSignals    # the shipping builder on this game


def labels(human_keep: list[Interval], n: int) -> np.ndarray:
    """1.0 for each second whose centre lies inside union(human_keep)."""
    centers = np.arange(n, dtype=np.float64) + 0.5
    y = np.zeros(n, dtype=np.float64)
    for s, e in union(human_keep):
        y[(centers >= s) & (centers <= e)] = 1.0
    return y


def prepare(game: dv.Game) -> TrainGame:
    features = compute_features(game.positions, game.contacts, game.duration,
                                game.frame_height)
    return TrainGame(
        test_id=game.test_id,
        source_video_md5=game.raw["source_video_md5"],
        duration=game.duration,
        human_keep=list(game.human_keep),
        features=features,
        y=labels(game.human_keep, features.shape[0]),
        guarded=evaluate_deadtime(game.human_keep, dv.v5_guarded(game), game.duration),
    )


# ── model ──────────────────────────────────────────────────────────────────

def fit_logistic(X: np.ndarray, y: np.ndarray, lr: float = LEARNING_RATE,
                 iters: int = ITERATIONS, l2: float = L2) -> tuple[np.ndarray, float]:
    """CF-173's full-batch gradient descent, unchanged: class-weighted log loss."""
    w = np.zeros(X.shape[1])
    b = 0.0
    sw = np.where(y == 1, POS_WEIGHT, 1.0)
    sw = sw / sw.mean()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = sw * (p - y)
        w -= lr * (X.T @ g / len(y) + l2 * w)
        b -= lr * float(g.mean())
    return w, b


def _columns(names: Sequence[str]) -> list[int]:
    return [FEATURE_NAMES.index(n) for n in names]


def fit_on(games: Sequence[TrainGame], lags: Sequence[int],
           columns: Sequence[str] = tuple(FEATURE_NAMES)) -> dict:
    """Fit on `games` and return a weights-shaped dict (no threshold)."""
    idx = _columns(columns)
    X = np.vstack([design_matrix(g.features[:, idx], list(lags)) for g in games])
    y = np.concatenate([g.y for g in games])
    coef, intercept = fit_logistic(X, y)
    return {"coef": coef, "intercept": intercept, "scaling": dict(SCALING),
            "context": context_rule(list(lags)), "columns": list(columns)}


def probabilities(game: TrainGame, model: dict, *, smooth: bool = True) -> np.ndarray:
    """Through `predict_in_play` — the shared inference, not a local copy."""
    idx = _columns(model["columns"])
    return predict_in_play(game.features[:, idx], model, smooth=smooth)


def score(game: TrainGame, smoothed: np.ndarray, threshold: float) -> DeadTimeSignals:
    windows = windows_from_probability(smoothed, threshold, game.duration)
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def pick_threshold(games: Sequence[TrainGame], model: dict) -> float:
    """The THRESHOLDS entry with the highest summed net over `games`.

    Summed over every game passed — the training games, test1 and test3
    included — because a threshold has to work on whatever it is given. Ties go
    to the lower threshold (strict >), as on the branch.
    """
    smoothed = [probabilities(g, model) for g in games]
    best_thr, best_u = THRESHOLDS[0], -np.inf
    for thr in THRESHOLDS:
        u = sum(net_seconds(score(g, p, thr)) for g, p in zip(games, smoothed))
        if u > best_u:
            best_u, best_thr = u, thr
    return best_thr


def auc(scores: np.ndarray, y: np.ndarray) -> float | None:
    """Rank AUC (Mann-Whitney), tied scores sharing their average rank.

    None when either class is empty — there is nothing to rank against.
    """
    scores = np.asarray(scores, dtype=np.float64)
    pos = np.asarray(y) == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    uniq, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    before = np.concatenate(([0], np.cumsum(counts)[:-1]))
    avg_rank = before + (counts + 1) / 2.0          # 1-based average rank per value
    ranks = avg_rank[inverse]
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


# ── leave-one-game-out ─────────────────────────────────────────────────────

def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd)


def run_fold(held: TrainGame, train: Sequence[TrainGame], lags: Sequence[int]) -> dict:
    model = fit_on(train, lags)
    thr = pick_threshold(train, model)
    sig = score(held, probabilities(held, model), thr)
    g = held.guarded
    return {
        "held_out": held.test_id,
        "threshold": round(thr, 2),
        "dead_removed_pct": _r(sig.dead_removed_pct or 0.0),
        "live_cut_sec": _r(sig.live_removed_sec),
        "net_sec": _r(net_seconds(sig)),
        "auc": _r(auc(probabilities(held, model, smooth=False), held.y)),
        "guarded_dead_removed_pct": _r(g.dead_removed_pct or 0.0),
        "guarded_live_cut_sec": _r(g.live_removed_sec),
        "guarded_net_sec": _r(net_seconds(g)),
        "coef": [_r(c) for c in model["coef"]],
        "intercept": _r(model["intercept"]),
    }


def loo(games: Sequence[TrainGame], lags: Sequence[int]) -> list[dict]:
    return [run_fold(held, [g for g in games if g is not held], lags) for held in games]


def comparable_net(folds: Sequence[dict]) -> float:
    return float(sum(f["net_sec"] for f in folds if f["held_out"] in COMPARABLE))


def choose_variant(variant_folds: dict[str, list[dict]]) -> str:
    """Highest summed held-out net over COMPARABLE; the first listed wins a tie."""
    best, best_net = "", -np.inf
    for name, folds in variant_folds.items():
        net = comparable_net(folds)
        if net > best_net:
            best, best_net = name, net
    return best


def ablation(games: Sequence[TrainGame], lags: Sequence[int]) -> dict[str, dict[str, float | None]]:
    """Held-out AUC per game for each ABLATION subset. Printed, not written."""
    out: dict[str, dict[str, float | None]] = {}
    for name, cols in ABLATION.items():
        per_game: dict[str, float | None] = {}
        for held in games:
            model = fit_on([g for g in games if g is not held], lags, cols)
            per_game[held.test_id] = auc(probabilities(held, model, smooth=False), held.y)
        out[name] = per_game
    return out


# ── the weights file ───────────────────────────────────────────────────────

def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out or "unknown"


def train(games: Sequence[TrainGame], commit: str) -> tuple[dict, dict]:
    """LOO both variants, pick one, fit it on everything. Returns (weights, report).

    Pure: no I/O, so two calls on the same games give the same weights.
    """
    variant_folds = {name: loo(games, lags) for name, lags in VARIANTS.items()}
    chosen = choose_variant(variant_folds)
    lags = VARIANTS[chosen]
    folds = variant_folds[chosen]
    threshold = float(np.median([f["threshold"] for f in folds]))
    model = fit_on(games, lags)

    weights = {
        "features": list(FEATURE_NAMES),
        "feature_version": FEATURE_VERSION,
        "scaling": dict(SCALING),
        "context": context_rule(list(lags)),
        "coef": [_r(c) for c in model["coef"]],
        "intercept": _r(model["intercept"]),
        "threshold": round(threshold, 2),
        "trained_on": [{"test_id": g.test_id, "source_video_md5": g.source_video_md5}
                       for g in games],
        "git_commit": commit,
        "folds": folds,
        "context_variants": {
            name: {
                "context": context_rule(list(VARIANTS[name])),
                "comparable_net_sec": _r(comparable_net(vf)),
                "folds": vf,
            }
            for name, vf in variant_folds.items()
        },
        # Always false from the trainer. CF-395 applies the epic's bar and is
        # the only thing that may flip it; committing weights is not enabling.
        "validated": False,
    }
    report = {"chosen": chosen, "variant_folds": variant_folds, "model": model}
    return weights, report


def serialize(weights: dict) -> str:
    return json.dumps(weights, indent=2) + "\n"


def write(weights: dict, path: Path = WEIGHTS_PATH) -> None:
    # newline="\n": on Windows text mode would otherwise write CRLF, and the
    # file would differ by platform.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(serialize(weights))


# ── report ─────────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v * 100:5.1f}%"


def _auc(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:.3f}"


def print_folds(name: str, folds: Sequence[dict]) -> None:
    print(f"\ncontext variant {name!r} - held-out, threshold picked on train games only;"
          f" net at {LIVE_CUT_COST:.0f}:1 live cut")
    print(f"  {'fixture':8s} {'thr':>4s}  {'dead%':>6s} {'live s':>7s} {'net s':>8s}"
          f" {'AUC':>5s}  | guarded {'dead%':>6s} {'live s':>7s} {'net s':>8s}")
    for f in folds:
        mark = "" if f["held_out"] in COMPARABLE else "  (excluded)"
        print(f"  {f['held_out']:8s} {f['threshold']:4.2f}  {_pct(f['dead_removed_pct'])}"
              f" {f['live_cut_sec']:7.1f} {f['net_sec']:+8.1f} {_auc(f['auc']):>5s}"
              f"  |         {_pct(f['guarded_dead_removed_pct'])}"
              f" {f['guarded_live_cut_sec']:7.1f} {f['guarded_net_sec']:+8.1f}{mark}")
    comp = [f for f in folds if f["held_out"] in COMPARABLE]
    print(f"  {'comparable three':16s}  live cut {sum(f['live_cut_sec'] for f in comp):6.1f}s"
          f" (guarded {sum(f['guarded_live_cut_sec'] for f in comp):6.1f}s)"
          f"   net {sum(f['net_sec'] for f in comp):+7.1f}s"
          f" (guarded {sum(f['guarded_net_sec'] for f in comp):+7.1f}s)")


def print_ablation(table: dict[str, dict[str, float | None]]) -> None:
    print("\nablation - held-out AUC per fixture, shipped context variant")
    ids = list(next(iter(table.values())).keys())
    print(f"  {'subset':14s} " + " ".join(f"{t:>6s}" for t in ids)
          + f" {'mean 2/4/5':>10s} {'mean 2-5':>9s} {'epic':>6s}")
    for name, per in table.items():
        comp = [v for t, v in per.items() if t in COMPARABLE and v is not None]
        two_five = [v for t, v in per.items() if t != "test1" and v is not None]
        print(f"  {name:14s} " + " ".join(f"{_auc(per[t]):>6s}" for t in ids)
              + f" {_auc(float(np.mean(comp)) if comp else None):>10s}"
              f" {_auc(float(np.mean(two_five)) if two_five else None):>9s}"
              f" {EPIC_AUC[name]:6.3f}")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="CF-393 leave-one-game-out in-play trainer")
    ap.add_argument("--no-write", action="store_true",
                    help="run LOO and print the report, but leave the weights file alone")
    ap.add_argument("--ablation", action="store_true",
                    help="also print held-out AUC for the epic's feature subsets")
    args = ap.parse_args(argv)

    games = [prepare(dv.load_game(t)) for t in TEST_IDS]
    for g in games:
        print(f"{g.test_id}: {g.duration:.0f}s, {int(g.y.sum())}s in-play "
              f"({g.y.mean():.0%}), md5 {g.source_video_md5}")

    weights, report = train(games, git_commit())
    for name, folds in report["variant_folds"].items():
        print_folds(name, folds)
    print(f"\nshipped variant: {report['chosen']!r} "
          f"(comparable net: " + ", ".join(
              f"{n} {comparable_net(f):+.1f}s" for n, f in report["variant_folds"].items())
          + f"); threshold {weights['threshold']:.2f} = median of "
          f"{[f['threshold'] for f in weights['folds']]}")

    if args.ablation:
        print_ablation(ablation(games, VARIANTS[report["chosen"]]))

    if args.no_write:
        print("\n--no-write: weights file untouched")
        return
    write(weights)
    print(f"\nweights -> {WEIGHTS_PATH} (validated=false; flipping it is CF-395's call)")


if __name__ == "__main__":
    main()
