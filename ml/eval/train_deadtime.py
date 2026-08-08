"""
Trainer for the learned dead-time keep-windows (ml/pipeline/dead_time_ml.py).

Fits the logistic weights against the hand-labeled in-play spans in the
dead-time fixtures and writes ml/pipeline/deadtime_ml_weights.json. Validation
is leave-one-game-out: every game's reported numbers come from a model that
never saw any second of it, with the decision threshold chosen on the training
games only. The shipped weights are then fit on all games with the median of
the per-fold thresholds.

Needs the R2 ball-cache JSON for each fixture, downloaded to --ball-cache-dir
as {source_video_md5}.json (see ml/eval/README.md). Everything else is local:
no video download, no app deps — runs on a laptop.

Usage:
  python -m ml.eval.train_deadtime --fixtures test1 test2 test3 test4 \
      --ball-cache-dir ml/eval/ball_caches
  python -m ml.eval.train_deadtime --fixtures test1 test2 --no-write  # dry run
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from ml.eval.harness import load_deadtime_fixture
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime, union
from ml.eval.tune_contacts import BRIDGE, COND  # drift-guarded copies of the condense settings
from ml.pipeline.ball import MODEL_ID, BallPosition, TrackedBall, find_contacts
from ml.pipeline.dead_time import active_windows_from_contacts, bridge_windows_by_motion
from ml.pipeline.dead_time_ml import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    WEIGHTS_PATH,
    active_windows_from_ml,
    compute_features,
)

# 1s of wrongly cut play costs this many seconds of kept dead time. Matches the
# repo posture (CF-46): keeping some dead time beats cutting play.
LIVE_CUT_COST = 4.0
POS_WEIGHT = 3.0            # class weight on in-play seconds during the fit
THRESHOLDS = np.arange(0.35, 0.91, 0.05)


@dataclass
class GameData:
    test_id: str
    duration: float
    frame_height: int
    positions: list[dict]
    contacts: list[dict]
    human_keep: list[tuple[float, float]]
    X: np.ndarray
    y: np.ndarray


def load_game(test_id: str, cache_dir: Path) -> GameData:
    fx = load_deadtime_fixture(test_id)
    md5 = fx.raw["source_video_md5"]
    frame_height = fx.raw.get("source_frame_height")
    if not frame_height:
        raise SystemExit(f"{test_id}: fixture needs source_frame_height (tracking-space px)")
    cache_path = cache_dir / f"{md5}.json"
    if not cache_path.exists():
        raise SystemExit(
            f"{test_id}: ball cache {cache_path} missing — download "
            f"ball-cache/{md5}-*.json from R2 (see ml/eval/README.md)"
        )
    positions = json.loads(cache_path.read_text())["positions"]
    tracker = TrackedBall(positions=[BallPosition(**p) for p in positions])
    contacts = find_contacts(tracker, frame_height=frame_height)
    human_keep = union(fx.keep)

    pos_dicts = [
        {"time": p["time"], "x": p["x"], "y": p["y"],
         "confidence": p.get("confidence", 0.0)}
        for p in positions
    ]
    X = compute_features(pos_dicts, contacts, fx.duration, frame_height)
    centers = np.arange(X.shape[0]) + 0.5
    y = np.zeros(X.shape[0])
    for s, e in human_keep:
        y[(centers >= s) & (centers <= e)] = 1.0
    return GameData(test_id, fx.duration, frame_height, pos_dicts, contacts,
                    human_keep, X, y)


def fit_logistic(Xz: np.ndarray, y: np.ndarray, lr: float = 0.1,
                 iters: int = 3000, l2: float = 1e-3) -> tuple[np.ndarray, float]:
    w = np.zeros(Xz.shape[1])
    b = 0.0
    sw = np.where(y == 1, POS_WEIGHT, 1.0)
    sw = sw / sw.mean()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Xz @ w + b)))
        g = sw * (p - y)
        w -= lr * (Xz.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return w, b


def make_weights(mean, std, coef, intercept, threshold) -> dict:
    return {
        "features": FEATURE_NAMES,
        "feature_version": FEATURE_VERSION,
        "mean": mean.tolist(), "std": std.tolist(),
        "coef": coef.tolist(), "intercept": float(intercept),
        "threshold": float(threshold),
    }


def score_baseline(game: GameData) -> DeadTimeSignals:
    """The shipping rule-based windows, for the per-game comparison line."""
    windows = active_windows_from_contacts(
        game.contacts, game.duration,
        gap_seconds=COND["gap_seconds"], pad_before=COND["pad_before"],
        pad_after=COND["pad_after"], min_contacts=int(COND["min_contacts"]),
        merge_gap_seconds=COND["merge_gap_seconds"],
    )
    windows = bridge_windows_by_motion(
        windows, game.positions,
        speed_pxps=BRIDGE["speed_pxps"], fast_fraction=BRIDGE["fast_fraction"],
        max_bridge_seconds=BRIDGE["max_bridge_seconds"],
        frame_height=game.frame_height,
    )
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def score_game(game: GameData, weights: dict) -> DeadTimeSignals:
    windows = active_windows_from_ml(
        game.positions, game.contacts, game.duration, game.frame_height,
        weights=weights,
    )
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def utility(sig: DeadTimeSignals) -> float:
    dead_removed_sec = (sig.dead_removed_pct or 0.0) * sig.human_dead_sec
    return dead_removed_sec - LIVE_CUT_COST * sig.live_removed_sec


def train_on(games: list[GameData]) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    X = np.vstack([g.X for g in games])
    y = np.concatenate([g.y for g in games])
    mean, std = X.mean(0), X.std(0)
    std = np.where(std > 0, std, 1.0)
    coef, intercept = fit_logistic((X - mean) / std, y)
    return mean, std, coef, intercept


def pick_threshold(games: list[GameData], mean, std, coef, intercept) -> float:
    best_thr, best_u = 0.5, -np.inf
    for thr in THRESHOLDS:
        w = make_weights(mean, std, coef, intercept, thr)
        u = sum(utility(score_game(g, w)) for g in games)
        if u > best_u:
            best_u, best_thr = u, float(thr)
    return best_thr


def fmt(sig: DeadTimeSignals) -> str:
    return (f"dead_removed={sig.dead_removed_pct:6.1%}  "
            f"live_cut={sig.live_removed_sec:5.1f}s ({sig.live_removed_pct:5.2%})  "
            f"keep={sig.condense_ratio:5.1%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--fixtures", nargs="+", required=True,
                    help="dead-time fixture test_ids to train on")
    ap.add_argument("--ball-cache-dir", type=Path, default=Path("ml/eval/ball_caches"))
    ap.add_argument("--no-write", action="store_true",
                    help="run LOGO validation but do not write the weights file")
    args = ap.parse_args()

    games = [load_game(t, args.ball_cache_dir) for t in args.fixtures]
    for g in games:
        print(f"{g.test_id}: {g.duration:.0f}s, {len(g.contacts)} contacts, "
              f"{int(g.y.sum())}s in-play ({g.y.mean():.0%}), frame_h={g.frame_height}")

    # ── Leave-one-game-out validation ─────────────────────────────────────────
    fold_thresholds: list[float] = []
    logo: list[dict] = []
    if len(games) >= 2:
        print("\nLeave-one-game-out (threshold picked on train games only):")
        for held in games:
            train_games = [g for g in games if g is not held]
            mean, std, coef, intercept = train_on(train_games)
            thr = pick_threshold(train_games, mean, std, coef, intercept)
            fold_thresholds.append(thr)
            sig = score_game(held, make_weights(mean, std, coef, intercept, thr))
            base = score_baseline(held)
            print(f"  held-out {held.test_id:8s} thr={thr:.2f}  ML    {fmt(sig)}")
            print(f"           {'':8s}          rules {fmt(base)}")
            logo.append({
                "held_out": held.test_id, "threshold": thr,
                "ml_dead_removed_pct": round(sig.dead_removed_pct or 0.0, 4),
                "ml_live_cut_sec": round(sig.live_removed_sec, 1),
                "rules_dead_removed_pct": round(base.dead_removed_pct or 0.0, 4),
                "rules_live_cut_sec": round(base.live_removed_sec, 1),
            })
    else:
        print("\nSingle game — skipping LOGO, threshold picked on the training data "
              "(optimistic; add fixtures for honest validation)")

    # ── Final model on all games ──────────────────────────────────────────────
    mean, std, coef, intercept = train_on(games)
    thr = (float(np.median(fold_thresholds)) if fold_thresholds
           else pick_threshold(games, mean, std, coef, intercept))
    weights = make_weights(mean.round(6), std.round(6), coef.round(6),
                           round(float(intercept), 6), round(thr, 2))

    print(f"\nFinal fit on {[g.test_id for g in games]}, shipped threshold {thr:.2f}")
    for name, c in sorted(zip(FEATURE_NAMES, coef), key=lambda t: -abs(t[1])):
        print(f"  {name:20s} {c:+.3f}")
    for g in games:  # trained-on numbers: sanity only, not validation
        print(f"  (fit) {g.test_id:8s} {fmt(score_game(g, weights))}")

    if args.no_write:
        print("\n--no-write: weights file untouched")
        return

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = "unknown"
    weights.update({
        # Always false from the trainer: a human flips it after reading the LOGO
        # report above. load_weights() warns while it is false, and
        # condense_use_ml should stay off until it is true.
        "validated": False,
        "leave_one_game_out": logo,
        "trained_on": args.fixtures,
        "trained_at": date.today().isoformat(),
        "ball_model": MODEL_ID,
        "git_commit": commit,
        "notes": (f"logistic, pos_weight={POS_WEIGHT}, threshold by "
                  f"{LIVE_CUT_COST:.0f}:1 live-cut utility on train folds; "
                  "LOGO thresholds " +
                  (str([round(t, 2) for t in fold_thresholds]) or "n/a")),
    })
    WEIGHTS_PATH.write_text(json.dumps(weights, indent=2) + "\n")
    print(f"\nweights → {WEIGHTS_PATH}  (validated=false — flip by hand once LOGO justifies it)")


if __name__ == "__main__":
    main()
