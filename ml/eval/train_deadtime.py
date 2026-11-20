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
  lagged copies at ±1/±3/±5s. The one with the better selection objective
  (below) over its leave-one-game-out folds ships; both are reported.
- **Two decoders** (CF-396) — smooth-and-threshold, and a two-state HMM
  decoded by Viterbi over the raw probabilities, its transitions fitted per
  fold on the training games' labels (`dead_time_ml.fit_transitions`). Each
  decoder picks its own context variant; `choose_decoder` applies the ticket's
  rule between them and the weights record the winner as `"decoder"`.

Kept from the branch: the numpy `fit_logistic`, leave-one-game-out validation,
a threshold picked on the training games only, and shipped weights fit on all
games at the median of the per-fold thresholds.

**One selection objective for every choice (CF-604)** — threshold, context
variant, decoder: `objective`. It sums net (at `LIVE_CUT_COST`) over the games
it is given **per game, not per row** — each game's net divided by its
duration, so test1's hour of 360p footage (54% of the rows) weighs what any
other game weighs — and counts test3 only through the bar's abstain clause
(`validate_learned` clause 4): a candidate whose test3 cuts more than
`TEST3_LIVE_CAP_SEC` of live play without abstaining loses to one that does
not, and test3's net never enters the sum.

**Nested leave-one-game-out (CF-604).** `select` is the whole shipping
procedure — leave-one-game-out over the games it is given, every choice made
on those folds, threshold the median of the chosen folds' — and `train` runs it
on all five games to make the shipped weights. Those folds (`"folds"`) are
therefore selection-biased as a verdict: the variant and decoder were chosen on
the same held-out scores. The unbiased verdict is `"nested"`: for each outer
game, `select` runs on the other four alone, the model it describes is fitted
on them, and only then is the outer game scored. That record carries per-rally
metrics (`rally_match`), the paired comparison against `guarded` (McNemar on
whole-rally deletions; a within-game block bootstrap of Δnet / Δlive,
descriptive — `paired_stats`) and the CF-389 bar applied to its folds.
`validate_learned` reads `"folds"`, unchanged in shape.

**The contact-scale position** (CF-174) is `--contact-scale`, applied as
`visualize_deadtime` applies it and recorded in the file as `"contact_scale"`;
the model is refitted at the position it is scored at. The default is the
settings mirror, as before.

**The weights are written with `"validated": false`, always.** Committing them
is not enabling them: the builder that reads this file
(`dead_time_ml.active_windows_learned`, CF-394) refuses unvalidated weights, so
`condense_mode="learned"` falls back to `guarded` on every game until the flag
flips, and the bar for flipping it is CF-395's to apply. The eval ladder's `v6`
scores them anyway, in-sample.

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
  python -m ml.eval.train_deadtime              # LOO both decoders + write the winner
  python -m ml.eval.train_deadtime --decoder threshold   # one decoder only, written as is
  python -m ml.eval.train_deadtime --no-write   # report only
  python -m ml.eval.train_deadtime --ablation   # also the feature-subset AUC table
  python -m ml.eval.train_deadtime --no-write --contact-scale off   # production's position
  python -m ml.eval.train_deadtime --no-write --jitter-draws 20     # label-jitter sensitivity
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ml.eval import deadtime_variants as dv
from ml.eval import paired_stats as ps
from ml.eval import rally_match as rm
from ml.eval import visualize_deadtime as vd
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime, union
from ml.eval.validate_learned import TEST3, TEST3_LIVE_CAP_SEC, apply_bar, scores_from_folds
from ml.eval.visualize_deadtime import COMPARABLE, LIVE_CUT_COST, TEST_IDS, net_seconds
from ml.pipeline.dead_time import Abstained, track_is_usable
from ml.pipeline.dead_time_ml import (
    CONTEXT_LAGS,
    DECODERS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    SCALING,
    WEIGHTS_PATH,
    compute_features,
    context_rule,
    decode_windows,
    design_matrix,
    fit_transitions,
    predict_in_play,
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
    guarded_windows: list[Interval]   # its keep-windows, for the per-rally scores
    # Production's learned chain declines this track (empty, or below the
    # builder's default min_track_rate) and falls back to `guarded`.
    declines: bool = False


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
    guarded = dv.v5_guarded(game)
    return TrainGame(
        test_id=game.test_id,
        source_video_md5=game.raw["source_video_md5"],
        duration=game.duration,
        human_keep=list(game.human_keep),
        features=features,
        y=labels(game.human_keep, features.shape[0]),
        guarded=evaluate_deadtime(game.human_keep, guarded, game.duration),
        guarded_windows=guarded,
        # The test `active_windows_learned` raises TrackTooSparseError on, at
        # its default floor.
        declines=not game.positions or not track_is_usable(game.speed_samples, game.duration),
    )


# ── the selection objective (CF-604) ───────────────────────────────────────
# Games that count only through bar clause 4 — abstain, or cut at most
# TEST3_LIVE_CAP_SEC of live play. Their net never enters the sum: test3 is a
# track the tracker cannot follow, and the bar judges it on that clause alone.
ABSTAIN_ONLY: tuple[str, ...] = (TEST3,)


@dataclass(frozen=True)
class Objective:
    """One candidate's standing over a set of games."""
    clause_4: bool      # every ABSTAIN_ONLY game present abstains or stays under the cap
    net: float          # sum over the other games of net_sec / duration
    live: float         # sum over the same games of live_cut_sec / duration

    @property
    def key(self) -> tuple[bool, float]:
        """What a choice maximises: the abstain clause first, then net."""
        return self.clause_4, self.net


def objective(rows: Iterable[tuple[TrainGame, float, float]]) -> Objective:
    """
    The selection objective over (game, net_sec, live_cut_sec) rows.

    Weighted per game, not per row: each game's seconds are divided by its
    duration, so the sum is net seconds per second of footage, added up game by
    game — test1's 54% of the rows counts as one game. An ABSTAIN_ONLY game
    counts through clause 4 alone: it passes when production's chain declines
    the track (`TrainGame.declines`, so `guarded` abstains there) or when it
    cuts at most TEST3_LIVE_CAP_SEC.
    """
    ok, net, live = True, 0.0, 0.0
    for game, net_sec, live_sec in rows:
        if game.test_id in ABSTAIN_ONLY:
            ok = ok and (game.declines or live_sec <= TEST3_LIVE_CAP_SEC)
            continue
        net += net_sec / game.duration
        live += live_sec / game.duration
    return Objective(ok, net, live)


def fold_objective(folds: Sequence[dict], games: Sequence[TrainGame]) -> Objective:
    """`objective` over a fold record's held-out rows."""
    by_id = {g.test_id: g for g in games}
    return objective((by_id[f["held_out"]], f["net_sec"], f["live_cut_sec"]) for f in folds)


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


def probabilities(game: TrainGame, model: dict) -> np.ndarray:
    """Through `predict_in_play` — the shared inference, not a local copy.

    Always the raw sigmoid: AUC is computed on it, and `decode_windows` does
    its own smoothing, so a smoothed trace here would be smoothed twice.
    """
    idx = _columns(model["columns"])
    return predict_in_play(game.features[:, idx], model, smooth=False)


def score(game: TrainGame, raw: np.ndarray, threshold: float) -> DeadTimeSignals:
    """Through `decode_windows` — the builder's dispatch — on the raw trace, so
    the threshold windows scored here are the ones the builder ships."""
    windows = decode_windows(raw, {"decoder": "threshold", "threshold": threshold},
                             game.duration)
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def pick_threshold(games: Sequence[TrainGame], model: dict) -> float:
    """The THRESHOLDS entry with the best `objective` over `games`.

    Over every game passed — the training games — by the same objective the
    variant and the decoder are chosen on (CF-604; it was raw summed seconds,
    which let test1 set the threshold). Ties go to the lower threshold
    (strict >), as on the branch.
    """
    raw = [probabilities(g, model) for g in games]
    best_thr: float = THRESHOLDS[0]
    best: tuple[bool, float] | None = None
    for thr in THRESHOLDS:
        sigs = [score(g, p, thr) for g, p in zip(games, raw)]
        u = objective((g, net_seconds(s), s.live_removed_sec) for g, s in zip(games, sigs)).key
        if best is None or u > best:
            best, best_thr = u, thr
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


def score_hmm(game: TrainGame, raw: np.ndarray, transitions: dict) -> DeadTimeSignals:
    """Through `decode_windows` — the builder's dispatch — on the raw trace."""
    windows = decode_windows(raw, {"decoder": "hmm", "hmm": transitions}, game.duration)
    return evaluate_deadtime(game.human_keep, windows, game.duration)


def run_fold(held: TrainGame, train: Sequence[TrainGame], lags: Sequence[int],
             decoder: str = "threshold") -> dict:
    """
    One held-out game. Under "hmm" the logistic fit and the threshold are the
    same as under "threshold" — the threshold is still picked and recorded, so
    hmm weights carry one — but the held-out windows come from Viterbi over the
    raw probabilities, with transitions fitted on the training games' labels
    only.
    """
    model = fit_on(train, lags)
    thr = pick_threshold(train, model)
    transitions: dict | None = None
    raw = probabilities(held, model)
    if decoder == "hmm":
        transitions = fit_transitions([g.y for g in train])
        sig = score_hmm(held, raw, transitions)
    else:
        sig = score(held, raw, thr)
    g = held.guarded
    fold: dict = {"held_out": held.test_id, "threshold": round(thr, 2)}
    if transitions is not None:
        fold["hmm"] = transitions
    return {
        **fold,
        "dead_removed_pct": _r(sig.dead_removed_pct or 0.0),
        "live_cut_sec": _r(sig.live_removed_sec),
        "net_sec": _r(net_seconds(sig)),
        "auc": _r(auc(raw, held.y)),
        "guarded_dead_removed_pct": _r(g.dead_removed_pct or 0.0),
        "guarded_live_cut_sec": _r(g.live_removed_sec),
        "guarded_net_sec": _r(net_seconds(g)),
        "coef": [_r(c) for c in model["coef"]],
        "intercept": _r(model["intercept"]),
    }


def loo(games: Sequence[TrainGame], lags: Sequence[int],
        decoder: str = "threshold") -> list[dict]:
    return [run_fold(held, [g for g in games if g is not held], lags, decoder)
            for held in games]


def comparable_net(folds: Sequence[dict]) -> float:
    return float(sum(f["net_sec"] for f in folds if f["held_out"] in COMPARABLE))


def comparable_live_cut(folds: Sequence[dict]) -> float:
    return float(sum(f["live_cut_sec"] for f in folds if f["held_out"] in COMPARABLE))


def choose_decoder(threshold_folds: Sequence[dict], hmm_folds: Sequence[dict],
                   games: Sequence[TrainGame]) -> str:
    """
    CF-396's shipping rule, on each decoder's own chosen-variant folds and in
    `objective`'s terms: "hmm" only if its per-game net is strictly higher than
    threshold's AND its per-game live play cut is not higher. Anything else — a
    tie on net included — keeps "threshold". A decoder failing the abstain
    clause loses to one passing it, whatever the seconds say.
    """
    t, h = fold_objective(threshold_folds, games), fold_objective(hmm_folds, games)
    if t.clause_4 != h.clause_4:
        return "hmm" if h.clause_4 else "threshold"
    return "hmm" if h.net > t.net and h.live <= t.live else "threshold"


def choose_variant(variant_folds: dict[str, list[dict]], games: Sequence[TrainGame]) -> str:
    """Best `objective` over each variant's held-out folds; the first listed
    wins a tie."""
    best, best_key = "", None
    for name, folds in variant_folds.items():
        key = fold_objective(folds, games).key
        if best_key is None or key > best_key:
            best, best_key = name, key
    return best


@dataclass
class Selection:
    """What the shipping procedure chose on one set of games, and its folds."""
    decoder: str
    variant: str
    threshold: float                            # median of the chosen folds'
    variants: dict[str, str]                    # decoder -> the variant it chose
    folds: dict[str, dict[str, list[dict]]]     # decoder -> variant -> LOO folds

    @property
    def chosen_folds(self) -> list[dict]:
        return self.folds[self.decoder][self.variant]


def select(games: Sequence[TrainGame], decoders: Sequence[str]) -> Selection:
    """
    The shipping procedure on `games` and nothing else: leave-one-game-out
    over them for every (decoder, variant), each decoder choosing its variant,
    `choose_decoder` between the two when both run, and the threshold the
    median of the chosen folds' thresholds.

    `train` calls it on all five games; `nested_fold` on the four an outer game
    leaves, which is what keeps that game out of every choice.
    """
    decoders = tuple(decoders)
    if decoders != DECODERS and not (len(decoders) == 1 and decoders[0] in DECODERS):
        raise ValueError(f"unknown decoder {decoders!r}, expected one of {list(DECODERS)}"
                         " or all of them")
    folds = {d: {name: loo(games, lags, d) for name, lags in VARIANTS.items()}
             for d in decoders}
    variants = {d: choose_variant(folds[d], games) for d in decoders}
    if len(decoders) == 1:
        decoder = decoders[0]
    else:
        decoder = choose_decoder(folds["threshold"][variants["threshold"]],
                                 folds["hmm"][variants["hmm"]], games)
    chosen = folds[decoder][variants[decoder]]
    threshold = round(float(np.median([f["threshold"] for f in chosen])), 2)
    return Selection(decoder, variants[decoder], threshold, variants, folds)


# ── nested leave-one-game-out (CF-604) ─────────────────────────────────────

def decode(game: TrainGame, raw: np.ndarray, decoder: str, threshold: float,
           transitions: dict | None) -> list[Interval]:
    """Held-out windows through `decode_windows`, the builder's dispatch."""
    model: dict = {"decoder": decoder, "threshold": threshold}
    if decoder == "hmm":
        model["hmm"] = transitions
    return decode_windows(raw, model, game.duration)


def _rounded(d: dict) -> dict:
    return {k: _rounded(v) if isinstance(v, dict)
            else _r(v) if isinstance(v, float) else v for k, v in d.items()}


def rally_record(game: TrainGame, windows: list[Interval]) -> tuple[dict, dict, np.ndarray]:
    """
    (rally, paired, unit deltas) for `windows` on `game`, against `guarded`.

    rally: `rally_match.summarize` for each side. paired: whole-rally deletions
    split by who made them, the exact McNemar p on the discordant ones, and the
    game's Δnet / Δlive cut (learned minus guarded). The unit deltas — Δnet and
    Δlive per (rally + following gap) — are what the block bootstrap resamples;
    they sum to the game's Δ and are not written.
    """
    learned = rm.match_rallies(game.human_keep, windows, game.duration)
    guarded = rm.match_rallies(game.human_keep, game.guarded_windows, game.duration)
    pairs = list(zip(learned, guarded))
    b = sum(lm.deleted and not gm.deleted for lm, gm in pairs)
    c = sum(gm.deleted and not lm.deleted for lm, gm in pairs)
    spans = rm.units(game.human_keep, game.duration)
    deltas = (ps.unit_scores(game.human_keep, windows, spans)
              - ps.unit_scores(game.human_keep, game.guarded_windows, spans))
    paired = {
        "learned_only_deleted": b,
        "guarded_only_deleted": c,
        "both_deleted": sum(lm.deleted and gm.deleted for lm, gm in pairs),
        "mcnemar_p": _r(ps.mcnemar_exact(b, c)),
        "delta_net_sec": _r(float(deltas[:, 0].sum())),
        "delta_live_cut_sec": _r(float(deltas[:, 1].sum())),
    }
    rally = {"learned": _rounded(rm.summarize(learned)),
             "guarded": _rounded(rm.summarize(guarded))}
    return rally, paired, deltas


def nested_fold(held: TrainGame, train: Sequence[TrainGame],
                decoders: Sequence[str]) -> tuple[dict, np.ndarray]:
    """
    One outer game, scored by the model `select` makes from `train` alone.

    Every choice — variant, decoder, threshold — and every fit — coefficients,
    hmm transitions — sees `train` only; `held` is scored and nothing else.
    The model is the one `train` would ship if `train` were all the games.
    Returns the fold record and its unit deltas (`rally_record`).
    """
    sel = select(train, decoders)
    model = fit_on(train, VARIANTS[sel.variant])
    raw = probabilities(held, model)
    transitions = fit_transitions([g.y for g in train]) if sel.decoder == "hmm" else None
    windows = decode(held, raw, sel.decoder, sel.threshold, transitions)
    sig = evaluate_deadtime(held.human_keep, windows, held.duration)
    g = held.guarded
    rally, paired, deltas = rally_record(held, windows)
    fold: dict = {"held_out": held.test_id, "variant": sel.variant, "decoder": sel.decoder,
                  "threshold": sel.threshold}
    if transitions is not None:
        fold["hmm"] = transitions
    fold |= {
        "dead_removed_pct": _r(sig.dead_removed_pct or 0.0),
        "live_cut_sec": _r(sig.live_removed_sec),
        "net_sec": _r(net_seconds(sig)),
        "auc": _r(auc(raw, held.y)),
        "guarded_dead_removed_pct": _r(g.dead_removed_pct or 0.0),
        "guarded_live_cut_sec": _r(g.live_removed_sec),
        "guarded_net_sec": _r(net_seconds(g)),
        "rally": rally,
        "paired": paired,
        "coef": [_r(c) for c in model["coef"]],
        "intercept": _r(model["intercept"]),
    }
    return fold, deltas


def _verdict(clauses: Sequence) -> dict:
    return {
        "passed": all(c.passed for c in clauses),
        "clauses": [{"name": c.name, "value": _r(c.value), "limit": _r(c.limit),
                     "margin": _r(c.margin), "passed": c.passed} for c in clauses],
    }


def nested_bar(folds: Sequence[dict], games: Sequence[TrainGame]) -> dict | None:
    """
    The CF-389 bar (`validate_learned.apply_bar`) on the nested folds, for both
    rungs. The folds score the builder with no track floor, so as they stand
    they are the ungated rung; the gated rung puts `guarded` — production's
    fallback — in place of every game the learned chain declines. None when the
    folds do not cover the games the bar names.
    """
    learned, guarded = scores_from_folds(folds)
    if not (set(COMPARABLE) | {TEST3}) <= set(learned):
        return None
    by_id = {g.test_id: g for g in games}
    guarded = {t: dataclasses.replace(s, abstained=isinstance(by_id[t].guarded_windows,
                                                               Abstained))
               for t, s in guarded.items()}
    gated = {t: guarded[t] if by_id[t].declines else s for t, s in learned.items()}
    return {"gated": _verdict(apply_bar(gated, guarded)),
            "ungated": _verdict(apply_bar(learned, guarded))}


def nested(games: Sequence[TrainGame], decoders: Sequence[str], *,
           draws: int = ps.BOOTSTRAP_DRAWS, seed: int = ps.BOOTSTRAP_SEED) -> dict:
    """
    The unbiased held-out record: `nested_fold` for every game, the paired
    comparison pooled over COMPARABLE, and the bar on the nested folds.

    The pooled McNemar adds the comparable games' discordant counts; its p is
    the primary test. The bootstrap interval is descriptive: it resamples units
    within each game and so ignores the variance between games. `draws=0`
    leaves the intervals out.
    """
    results = [nested_fold(h, [g for g in games if g is not h], decoders) for h in games]
    folds = [f for f, _ in results]
    comp = [(f, d) for f, d in results if f["held_out"] in COMPARABLE]
    b = sum(f["paired"]["learned_only_deleted"] for f, _ in comp)
    c = sum(f["paired"]["guarded_only_deleted"] for f, _ in comp)
    pooled: dict = {
        "games": [f["held_out"] for f, _ in comp],
        "learned_only_deleted": b,
        "guarded_only_deleted": c,
        "both_deleted": sum(f["paired"]["both_deleted"] for f, _ in comp),
        "mcnemar_p": _r(ps.mcnemar_exact(b, c)),
        "delta_net_sec": _r(sum(float(d[:, 0].sum()) for _, d in comp)),
        "delta_live_cut_sec": _r(sum(float(d[:, 1].sum()) for _, d in comp)),
    }
    if draws > 0 and comp:
        boot = ps.block_bootstrap([d for _, d in comp], draws, seed)
        pooled |= {
            "delta_net_ci95": [_r(x) for x in ps.percentile_interval(boot[:, 0])],
            "delta_live_cut_ci95": [_r(x) for x in ps.percentile_interval(boot[:, 1])],
            "bootstrap": {"draws": draws, "seed": seed,
                          "unit": "rally + following gap, resampled within each game",
                          "descriptive": True},
        }
    return {
        "decoders": list(decoders),
        "objective": f"per-game net_sec / duration at {LIVE_CUT_COST:.0f}:1; "
                     f"{'+'.join(ABSTAIN_ONLY)} through bar clause 4 only",
        "folds": folds,
        "paired": pooled,
        "bar": nested_bar(folds, games),
    }


# ── label-jitter sensitivity (CF-604) ──────────────────────────────────────

JITTER_SEC = 1.0


def jitter_keep(keep: Sequence[Interval], duration: float, rng: np.random.Generator,
                amount: float = JITTER_SEC) -> list[Interval]:
    """Every endpoint moved by its own uniform draw in ±`amount`, clamped to
    the video; a span the draws empty is dropped."""
    out = []
    for s, e in keep:
        js = min(max(s + rng.uniform(-amount, amount), 0.0), duration)
        je = min(max(e + rng.uniform(-amount, amount), 0.0), duration)
        if je > js:
            out.append((float(js), float(je)))
    return sorted(out)


def relabel(game: TrainGame, keep: list[Interval]) -> TrainGame:
    """`game` under other labels: y and guarded's score follow them; the
    features and guarded's windows, which never read the labels, do not."""
    return dataclasses.replace(
        game, human_keep=keep, y=labels(keep, game.features.shape[0]),
        guarded=evaluate_deadtime(keep, game.guarded_windows, game.duration))


def ranking(record: dict) -> dict:
    """Learned vs guarded from a `nested` record, pooled over COMPARABLE: ahead
    on net (Δnet > 0), and ahead on whole-rally deletions (fewer learned-only
    than guarded-only)."""
    p = record["paired"]
    return {"delta_net_sec": p["delta_net_sec"],
            "learned_ahead_on_net": p["delta_net_sec"] > 0,
            "learned_ahead_on_deletions":
                p["learned_only_deleted"] < p["guarded_only_deleted"]}


def sensitivity(games: Sequence[TrainGame], decoders: Sequence[str], baseline: dict,
                draws: int, seed: int = 0) -> dict:
    """
    `nested` rerun `draws` times on labels jittered by `jitter_keep` (seeded),
    and how often the learned-vs-guarded ranking differs from `baseline`'s, the
    unjittered record. A ranking that flips under ±1s of labelling noise is not
    a result. Printed, not written.
    """
    rng = np.random.default_rng(seed)
    base = ranking(baseline)
    runs = []
    for _ in range(draws):
        jittered = [relabel(g, jitter_keep(g.human_keep, g.duration, rng)) for g in games]
        runs.append(ranking(nested(jittered, decoders, draws=0)))
    return {
        "draws": draws, "seed": seed, "jitter_sec": JITTER_SEC, "baseline": base,
        "net_flips": sum(r["learned_ahead_on_net"] != base["learned_ahead_on_net"]
                         for r in runs),
        "deletion_flips": sum(r["learned_ahead_on_deletions"]
                              != base["learned_ahead_on_deletions"] for r in runs),
        "runs": runs,
    }


def ablation(games: Sequence[TrainGame], lags: Sequence[int]) -> dict[str, dict[str, float | None]]:
    """Held-out AUC per game for each ABLATION subset. Printed, not written."""
    out: dict[str, dict[str, float | None]] = {}
    for name, cols in ABLATION.items():
        per_game: dict[str, float | None] = {}
        for held in games:
            model = fit_on([g for g in games if g is not held], lags, cols)
            per_game[held.test_id] = auc(probabilities(held, model), held.y)
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


def train(games: Sequence[TrainGame], commit: str, decoder: str = "threshold",
          contact_scale: str | None = None) -> tuple[dict, dict]:
    """`select` on every game, fit its choice on everything, and run the
    nested record beside it. Returns (weights, report).

    `decoder` is one of DECODERS, run alone, or "auto": both, and
    `choose_decoder` between them — in the shipped selection and in every
    nested one alike. Each decoder picks its own context variant. Under "hmm"
    the weights also carry the transitions fitted on every game's labels,
    beside the threshold (the median fold threshold, as under "threshold").

    `contact_scale` is recorded, not applied: the games were loaded at it
    (`main` applies it before `load_game`). None records the settings mirror's
    current position, which is what the games were loaded at unless a caller
    moved it.

    Pure: no I/O, so two calls on the same games give the same weights.
    """
    if decoder != "auto" and decoder not in DECODERS:
        raise ValueError(f"unknown decoder {decoder!r}, expected one of {list(DECODERS)}")
    decoders = DECODERS if decoder == "auto" else (decoder,)
    sel = select(games, decoders)
    chosen, lags = sel.variant, VARIANTS[sel.variant]
    variant_folds = sel.folds[sel.decoder]
    model = fit_on(games, lags)

    weights: dict = {
        "features": list(FEATURE_NAMES),
        "feature_version": FEATURE_VERSION,
        "scaling": dict(SCALING),
        "context": context_rule(list(lags)),
        "coef": [_r(c) for c in model["coef"]],
        "intercept": _r(model["intercept"]),
        "threshold": sel.threshold,
        "decoder": sel.decoder,
    }
    if sel.decoder == "hmm":
        weights["hmm"] = fit_transitions([g.y for g in games])
    record = nested(games, decoders)
    weights |= {
        "trained_on": [{"test_id": g.test_id, "source_video_md5": g.source_video_md5}
                       for g in games],
        "git_commit": commit,
        # The shipped selection's own folds: what `validate_learned` reads.
        # Selection-biased as a verdict — see `"nested"` for the unbiased one.
        "folds": sel.chosen_folds,
        "context_variants": {
            name: {
                "context": context_rule(list(VARIANTS[name])),
                "comparable_net_sec": _r(comparable_net(vf)),
                "folds": vf,
            }
            for name, vf in variant_folds.items()
        },
        "contact_scale": contact_scale or vd.mirror_position(),
        "nested": record,
        # Always false from the trainer. CF-395 applies the epic's bar and is
        # the only thing that may flip it; committing weights is not enabling.
        # A nested bar pass does not flip it either (CF-399).
        "validated": False,
    }
    report = {"chosen": chosen, "variant_folds": variant_folds, "model": model,
              "selection": sel, "nested": record}
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


def print_folds(name: str, folds: Sequence[dict], decoder: str = "threshold") -> None:
    print(f"\n[{decoder}] context variant {name!r} - held-out, threshold picked on train"
          f" games only; net at {LIVE_CUT_COST:.0f}:1 live cut")
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
    for f in folds:
        if "hmm" in f:
            print(f"  {f['held_out']:8s} transitions from the other four: {f['hmm']}")


def _del(d: dict) -> str:
    return "/".join(str(d[name]) for name, _ in rm.LENGTH_BUCKETS)


def _err(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:+5.1f}"


def print_nested(record: dict, contact_scale: str) -> None:
    folds = record["folds"]
    print(f"\nnested leave-one-game-out (CF-604), contact scale {contact_scale}: every"
          " choice made on the\nother four games alone; the outer game only scores."
          f" Objective: {record['objective']}.")
    print(f"  {'fixture':8s} {'variant':10s} {'decoder':9s} {'thr':>4s}  {'dead%':>6s}"
          f" {'live s':>7s} {'net s':>8s}  | guarded {'dead%':>6s} {'live s':>7s} {'net s':>8s}")
    for f in folds:
        mark = "" if f["held_out"] in COMPARABLE else "  (excluded)"
        print(f"  {f['held_out']:8s} {f['variant']:10s} {f['decoder']:9s} {f['threshold']:4.2f}"
              f"  {_pct(f['dead_removed_pct'])} {f['live_cut_sec']:7.1f} {f['net_sec']:+8.1f}"
              f"  |         {_pct(f['guarded_dead_removed_pct'])}"
              f" {f['guarded_live_cut_sec']:7.1f} {f['guarded_net_sec']:+8.1f}{mark}")
    print("\n  per rally, learned | guarded: recall at >=50% kept, at 100% kept; whole"
          " rallies deleted\n  <=3s/3-8s/>8s; median start and end error (s, + = late"
          " start / kept tail)")
    for f in folds:
        cells = []
        for side in ("learned", "guarded"):
            r = f["rally"][side]
            cells.append(f"{_pct(r['recall_50'])} {_pct(r['recall_100'])} {_del(r['deleted']):>8s}"
                         f" {_err(r['start_err_median_sec'])} {_err(r['end_err_median_sec'])}")
        p = f["paired"]
        print(f"  {f['held_out']:8s} n={f['rally']['learned']['rallies']:<3d} {cells[0]} | {cells[1]}"
              f"   deleted by learned only {p['learned_only_deleted']}, guarded only"
              f" {p['guarded_only_deleted']}, McNemar p {p['mcnemar_p']:.3f}")
    p = record["paired"]
    print(f"\n  paired vs guarded over {'+'.join(p['games'])}: whole rallies deleted by learned"
          f" only {p['learned_only_deleted']}, by guarded only {p['guarded_only_deleted']}"
          f" (both {p['both_deleted']}); exact McNemar p = {p['mcnemar_p']:.4f} (primary)")
    ci = ""
    if "delta_net_ci95" in p:
        ci = (f"; within-game block bootstrap 95% [{p['delta_net_ci95'][0]:+.1f},"
              f" {p['delta_net_ci95'][1]:+.1f}]s net, [{p['delta_live_cut_ci95'][0]:+.1f},"
              f" {p['delta_live_cut_ci95'][1]:+.1f}]s live (descriptive: ignores"
              " between-game variance)")
    print(f"  Δnet {p['delta_net_sec']:+.1f}s, Δlive cut {p['delta_live_cut_sec']:+.1f}s{ci}")
    if record["bar"] is None:
        print("  bar: n/a (the folds do not cover the games the bar names)")
        return
    for rung in ("gated", "ungated"):
        v = record["bar"][rung]
        print(f"\n  CF-389 bar on the nested folds, learned {rung}: "
              f"{'PASS' if v['passed'] else 'FAIL'}")
        for c in v["clauses"]:
            print(f"    {'pass' if c['passed'] else 'FAIL'}  {c['name']:44s} value"
                  f" {c['value']:8.2f}  limit {c['limit']:8.2f}  margin {c['margin']:+8.2f}")


def print_sensitivity(s: dict) -> None:
    b = s["baseline"]
    print(f"\nlabel jitter (every endpoint ±{s['jitter_sec']:.0f}s uniform, {s['draws']} draws,"
          f" seed {s['seed']}): unjittered learned is "
          f"{'ahead of' if b['learned_ahead_on_net'] else 'not ahead of'} guarded on net"
          f" (Δ {b['delta_net_sec']:+.1f}s) and "
          f"{'ahead' if b['learned_ahead_on_deletions'] else 'not ahead'} on whole-rally"
          " deletions")
    print(f"  ranking flips: net {s['net_flips']}/{s['draws']},"
          f" deletions {s['deletion_flips']}/{s['draws']}"
          + ("" if s["net_flips"] or s["deletion_flips"] else " - stable"))
    print("  Δnet per draw: " + ", ".join(f"{r['delta_net_sec']:+.1f}" for r in s["runs"]))


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
    ap.add_argument("--decoder", choices=("auto",) + DECODERS, default="auto",
                    help="how probabilities become windows (CF-396). 'auto' (default) runs"
                         " both leave-one-game-out and writes the one choose_decoder"
                         " picks; naming one runs and writes only that one")
    ap.add_argument("--contact-scale", choices=vd.CONTACT_SCALE_POSITIONS,
                    default=vd.mirror_position(),
                    help="side of the CF-174 switch to load, fit and score at: off"
                         " (production), on, or on with CF-376's soft flag (default: the"
                         " settings mirror, currently %(default)s). Recorded in the file")
    ap.add_argument("--jitter-draws", type=int, default=0, metavar="N",
                    help="also rerun the nested record N times with every label endpoint"
                         " jittered by up to ±1s, and report whether learned-vs-guarded"
                         " flips (printed, not written; each draw is a full nested run)")
    ap.add_argument("--jitter-seed", type=int, default=0,
                    help="seed for --jitter-draws (default %(default)s)")
    args = ap.parse_args(argv)

    # The position is for this run only, restored on the way out as
    # visualize_deadtime.main restores it: load_game reads it at call time.
    saved = (dv.NORMALIZE, dv.SOFT_SCALE)
    try:
        vd.apply_contact_scale(args.contact_scale)
        _run(args)
    finally:
        dv.NORMALIZE, dv.SOFT_SCALE = saved


def _run(args: argparse.Namespace) -> None:
    games = [prepare(dv.load_game(t)) for t in TEST_IDS]
    print(f"contact scale {args.contact_scale}")
    for g in games:
        print(f"{g.test_id}: {g.duration:.0f}s, {int(g.y.sum())}s in-play "
              f"({g.y.mean():.0%}), md5 {g.source_video_md5}"
              + (", learned chain declines the track" if g.declines else ""))

    weights, report = train(games, git_commit(), args.decoder, args.contact_scale)
    sel: Selection = report["selection"]
    for decoder, variant_folds in sel.folds.items():
        for name, folds in variant_folds.items():
            print_folds(name, folds, decoder)
        print(f"\n[{decoder}] chosen variant: {sel.variants[decoder]!r} (objective, per-game"
              " net/s: " + ", ".join(
                  f"{n} {fold_objective(f, games).net:+.4f}" for n, f in variant_folds.items())
              + "; comparable net: " + ", ".join(
                  f"{n} {comparable_net(f):+.1f}s" for n, f in variant_folds.items()) + ")")
    print(f"\nshipped: decoder {sel.decoder!r}, variant {sel.variant!r}, threshold"
          f" {weights['threshold']:.2f} = median of {[f['threshold'] for f in weights['folds']]}"
          + (f"; transitions {weights['hmm']}" if "hmm" in weights else ""))
    if args.decoder == "auto":
        t = fold_objective(sel.folds["threshold"][sel.variants["threshold"]], games)
        h = fold_objective(sel.folds["hmm"][sel.variants["hmm"]], games)
        print(f"decoder: {sel.decoder!r} - per-game net threshold {t.net:+.4f} vs hmm"
              f" {h.net:+.4f}; per-game live cut threshold {t.live:.4f} vs hmm {h.live:.4f}"
              " (hmm ships only if net rises and live cut does not)")
    print("The folds above chose among themselves, so they are selection-biased as a verdict;"
          " the nested record below is not.")
    print_nested(report["nested"], args.contact_scale)

    if args.jitter_draws > 0:
        decoders = DECODERS if args.decoder == "auto" else (args.decoder,)
        print_sensitivity(sensitivity(games, decoders, report["nested"], args.jitter_draws,
                                      args.jitter_seed))

    if args.ablation:
        print_ablation(ablation(games, VARIANTS[report["chosen"]]))

    if args.no_write:
        print("\n--no-write: weights file untouched")
        return
    write(weights)
    print(f"\nweights ({sel.decoder}, contact scale {args.contact_scale}) -> {WEIGHTS_PATH} "
          "(validated=false; flipping it is CF-395's call)")


if __name__ == "__main__":
    main()
