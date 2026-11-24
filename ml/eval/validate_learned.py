"""
Score the learned builder held out and apply the epic's validation bar (CF-395).

The shipped weights (`ml/pipeline/deadtime_ml_weights.json`) were fitted on all
five fixtures, so scoring them on those fixtures — the ladder's `v6` — is
in-sample and cannot judge them. The held-out models are the trainer's
leave-one-game-out folds (CF-393), which the weights file already carries under
`"folds"`: each fold's coefficients, intercept and threshold — and, under the
hmm decoder, its transitions — came from the other four games only. This
module rebuilds a weights dict per fold from that record (it does not refit),
runs it through the production builder (`dead_time_ml.active_windows_learned`)
on the one game the fold held out, and scores the result beside `guarded`
(`deadtime_variants.v5_guarded`) on the same game.

**Held out is not quite clean: the context variant was chosen on the held-out
games.** The folds scored are those of the context variant the trainer picked
(`train_deadtime.choose_variant`), and it picks the variant with the highest
summed held-out net over test2/4/5 — the very quantity clause 1 tests. So the
coefficients, intercept and threshold never saw the held-out game, but the
choice of variant did, and clause 1 on the chosen variant is selection-biased.
On the committed record the chosen variant (`none`, +463.27s) passes clause 1
and the other (`lags_1_3_5`, +434.77s) would fail it. `clause_1_by_variant`
reports clause 1 for every recorded variant so the report shows this; removing
the bias needs the trainer to choose the variant inside each training fold
(nested), which it does not yet do.

**An hmm verdict carries a second selection bias: the decoder was chosen on the
same held-out games.** Under `--decoder auto` the trainer writes
`"decoder": "hmm"` only when `train_deadtime.choose_decoder` finds hmm's summed
held-out net over COMPARABLE strictly above threshold's — again clause 1's own
quantity, on the folds this module then scores. So a clause 1 pass on an hmm
file has seen the judged games twice: once through the variant, once through
the decoder. The losing decoder's folds are not recorded, so unlike the variant
this bias cannot be shown beside the verdict; the report states it instead.
Removing it is CF-604's nested selection, not this module's.

Two learned rungs, both held out:

- **gated** — production's chain: the builder declines a track below
  `min_track_rate` usable speed samples/s (`TrackTooSparseError`) and the
  condense stage falls back to `guarded`, which abstains there. This is the rung
  the bar is judged on, because it is what `condense_mode="learned"` would ship.
- **ungated** — the same builder with `min_track_rate=0.0`, so it judges every
  game with a track it can measure (D-0034). Eval only: production's gate is
  unchanged. On the fixtures it differs from gated on test3 alone, the one game
  below the gate. A track with no usable speed sample — empty, a single point,
  or points further apart than `speed_samples` pairs — still falls back to
  `guarded`: with nothing to score the builder would return `[]` and cut
  everything, so `learned_windows` declines it, as the gate would.

The bar (CF-389 decision 6) is `apply_bar`, with the epic's numbers as the
constants below. It is pure, so the committed fold record — which carries the
learned and `guarded` scores per fold — can be held to it in CI, where the ball
caches are absent (`ml/tests/test_validate_learned.py`).

**Scored at the CF-174 switch on** (`ball_contact_scale_enabled`, the code
default and the trainer's mirror), with CF-229's static-site filter off: that
is the position the folds were fitted and scored at, and the position of
CF-391's `guarded` column the bar is measured against. The switch-off position
is not scored here.

This reports; it never writes the weights file. `"validated"` is a person's
edit, made only when `apply_bar` passes. The CLI exits 1 when the bar fails for
the gated rung and 0 when it passes, so a script can gate on it.

Needs the five ball caches in `ml/eval/ball_caches/` (CF-390). No video, no
app deps.

Usage:
  python -m ml.eval.validate_learned
  python -m ml.eval.validate_learned --dump-windows DIR
  # -> DIR/{test}_learned.json, DIR/{test}_learned-ungated.json
  #    (held-out keep-windows, for harness --windows-json)
"""
from __future__ import annotations

import argparse
import copy
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ml.eval import deadtime_variants as dv
from ml.eval import visualize_deadtime as vd
from ml.eval.metrics import evaluate_deadtime
from ml.eval.visualize_deadtime import COMPARABLE, TEST_IDS, net_seconds
from ml.pipeline import dead_time_ml
from ml.pipeline.dead_time import Abstained, speed_samples
from ml.pipeline.dead_time_ml import TrackTooSparseError, active_windows_learned, load_weights
from ml.pipeline.intervals import Interval

# ── the bar: CF-389 decision 6, verbatim ───────────────────────────────────
# Held out, against CF-391's re-measured `guarded`, over COMPARABLE:
#   1. summed net seconds exceed guarded's                       (strict >)
#   2. summed live play cut is at most guarded's + 15s           (<=)
#   3. none of the three cuts more than 30s more live than guarded (<=)
#   4. test3 either abstains or cuts at most 30s of live play     (<=)
SUMMED_LIVE_SLACK_SEC = 15.0
PER_GAME_LIVE_SLACK_SEC = 30.0
TEST3_LIVE_CAP_SEC = 30.0
TEST3 = "test3"

# The two rungs' names, also the dump-file keys ({test}_{key}.json).
GATED = "learned"
UNGATED = "learned-ungated"

# How each decoder's fold is rebuilt: the fold-record key that holds the
# decoder's own fitted parameters, which replaces the weights-level record of
# the same name, or None when the coefficients, intercept and threshold are the
# whole model. "hmm" is CF-396's per-fold transitions, fitted on that fold's
# training games only. A decoder with fitted parameters cannot borrow the
# shipped file's — those saw the held-out game — so a decoder that ships in
# `dead_time_ml.DECODERS` is not validatable here until it has an entry
# (`test_every_dispatched_decoder_says_how_its_folds_are_rebuilt` fails until
# then, and `fold_weights` refuses it).
_FOLD_RECORD: dict[str, str | None] = {"threshold": None, "hmm": "hmm"}
# What a fold record may carry. Anything else is a field this module does not
# know how to apply, and scoring past it would report a model the file does not
# describe.
_FOLD_KEYS = frozenset({
    "held_out", "threshold", "dead_removed_pct", "live_cut_sec", "net_sec", "auc",
    "guarded_dead_removed_pct", "guarded_live_cut_sec", "guarded_net_sec",
    "coef", "intercept",
}) | {r for r in _FOLD_RECORD.values() if r is not None}
# The decoders the production builder applies: exactly `dead_time_ml`'s own
# dispatch list, read from it rather than restated (CF-605).
# `active_windows_learned` decodes through `decode_windows`, which dispatches on
# the weights' "decoder"; a missing key is the threshold path. Dispatched is
# necessary, not sufficient: the decoder also needs a `_FOLD_RECORD` entry.
KNOWN_DECODERS = dead_time_ml.DECODERS


@dataclass(frozen=True)
class Score:
    """One builder on one game, in the units the bar is written in."""
    dead_removed_pct: float     # 0..1
    live_cut_sec: float
    net_sec: float              # at LIVE_CUT_COST
    abstained: bool = False


@dataclass(frozen=True)
class Clause:
    name: str
    value: float
    limit: float
    passed: bool
    detail: str = ""

    @property
    def margin(self) -> float:
        """Signed so that positive is a pass: headroom under a cap, or the lead
        over a floor. Zero passes a cap (`<=`) and fails the floor (`>`)."""
        if self.name.startswith("1"):
            return self.value - self.limit
        return self.limit - self.value


def apply_bar(learned: Mapping[str, Score], guarded: Mapping[str, Score]) -> list[Clause]:
    """
    The four clauses of CF-389 decision 6, in order. `validated` is
    `all(c.passed for c in clauses)`.

    Clause 3 is one entry per comparable game, so a reader sees which game
    fails and by how much rather than only the worst.
    """
    net = sum(learned[t].net_sec for t in COMPARABLE)
    g_net = sum(guarded[t].net_sec for t in COMPARABLE)
    live = sum(learned[t].live_cut_sec for t in COMPARABLE)
    g_live = sum(guarded[t].live_cut_sec for t in COMPARABLE)
    clauses = [
        Clause("1 summed net > guarded's", net, g_net, net > g_net,
               f"over {'+'.join(COMPARABLE)}"),
        Clause(f"2 summed live cut <= guarded's + {SUMMED_LIVE_SLACK_SEC:.0f}s", live,
               g_live + SUMMED_LIVE_SLACK_SEC, live <= g_live + SUMMED_LIVE_SLACK_SEC,
               f"guarded {g_live:.2f}s"),
    ]
    for t in COMPARABLE:
        extra = learned[t].live_cut_sec - guarded[t].live_cut_sec
        clauses.append(Clause(
            f"3 {t} live cut <= guarded's + {PER_GAME_LIVE_SLACK_SEC:.0f}s", extra,
            PER_GAME_LIVE_SLACK_SEC, extra <= PER_GAME_LIVE_SLACK_SEC,
            f"learned {learned[t].live_cut_sec:.2f}s, guarded {guarded[t].live_cut_sec:.2f}s",
        ))
    # An abstain cuts no live play, so its value is its own 0s; it passes on
    # the abstain, not on the cap.
    t3 = learned[TEST3]
    clauses.append(Clause(
        f"4 {TEST3} abstains or cuts <= {TEST3_LIVE_CAP_SEC:.0f}s live",
        t3.live_cut_sec, TEST3_LIVE_CAP_SEC,
        t3.abstained or t3.live_cut_sec <= TEST3_LIVE_CAP_SEC,
        "abstains" if t3.abstained else "condenses",
    ))
    return clauses


def scores_from_folds(folds: Sequence[dict]) -> tuple[dict[str, Score], dict[str, Score]]:
    """(learned, guarded) per game from the trainer's fold record.

    The trainer scores each fold through its decoder (`decode_windows`:
    `windows_from_probability` under threshold, Viterbi under hmm) with no
    track-rate gate, so its learned figures are the **ungated** rung. Neither
    side is marked abstained: the record keeps no windows, and test3's guarded
    row (0% removed, 0s cut) is its abstain.
    """
    learned, guarded = {}, {}
    for f in folds:
        t = f["held_out"]
        learned[t] = Score(f["dead_removed_pct"], f["live_cut_sec"], f["net_sec"])
        guarded[t] = Score(f["guarded_dead_removed_pct"], f["guarded_live_cut_sec"],
                           f["guarded_net_sec"])
    return learned, guarded


@dataclass(frozen=True)
class VariantClause1:
    """Clause 1 for one recorded context variant, from its fold record."""
    variant: str
    chosen: bool                # the variant whose folds this module scores
    clause: Clause


def clause_1_by_variant(weights: Mapping) -> list[VariantClause1]:
    """
    Clause 1 for every context variant the trainer recorded, from each
    variant's own fold record (ungated, which on COMPARABLE equals gated).

    The selection caveat this makes visible: the trainer picks the variant by
    the highest summed held-out net over COMPARABLE (`train_deadtime.
    choose_variant`) — the quantity clause 1 tests — so the chosen variant's
    clause 1 has seen the games it is judged on. A clause 1 pass that holds
    for the chosen variant only is a product of that choice.

    The chosen variant is the one whose `context` matches the shipped file's.
    Empty when the file records no variants.
    """
    out = []
    for name, rec in (weights.get("context_variants") or {}).items():
        learned, guarded = scores_from_folds(rec["folds"])
        out.append(VariantClause1(name, rec.get("context") == weights.get("context"),
                                  apply_bar(learned, guarded)[0]))
    return out


# ── per-fold weights ───────────────────────────────────────────────────────

def fold_weights(weights: dict, fold: dict) -> dict:
    """
    The shipped weights dict with one fold's model in place of the shipped one.

    Everything that describes preparation — features, version, scaling, context
    — is the shipped file's, which is also what the folds were fitted under
    (the trainer records the chosen variant's folds). `validated` is forced
    false: a fold model is never a shipping model.

    Refuses a fold this code does not know how to apply, a decoder
    `dead_time_ml` does not dispatch, and a dispatched decoder with no
    `_FOLD_RECORD` entry. A decoder's own record (under "hmm", the transitions)
    is the fold's, replacing the shipped file's: those were fitted on every
    game, the held-out one included, so decoding a held-out game with them
    would leak its labels into its own score. A fold without its decoder's
    record is refused for the same reason rather than borrowing the shipped
    one. Other decoders' records, in the fold or the shipped file, are not
    carried over: nothing decodes with them.
    """
    where = f"fold {fold.get('held_out')!r}"
    unknown = set(fold) - _FOLD_KEYS
    if unknown:
        raise ValueError(f"{where} carries unknown keys {sorted(unknown)}")
    decoder = weights.get("decoder", dead_time_ml.DEFAULT_DECODER)
    if decoder not in KNOWN_DECODERS:
        raise ValueError(f"weights use decoder {decoder!r}; this module knows "
                         f"{KNOWN_DECODERS}")
    if decoder not in _FOLD_RECORD:
        raise ValueError(f"weights use decoder {decoder!r}, which dead_time_ml "
                         "dispatches but this module cannot rebuild per fold; add "
                         "it to _FOLD_RECORD")
    if "threshold" not in fold:
        raise ValueError(f"{where} carries no threshold")
    record = _FOLD_RECORD[decoder]
    if record is not None and record not in fold:
        raise ValueError(f"{where} carries no {record!r} record, which a {decoder} "
                         "model needs fitted on its own training games")
    out = copy.deepcopy(weights)
    out["coef"] = list(fold["coef"])
    out["intercept"] = fold["intercept"]
    out["threshold"] = fold["threshold"]
    for other in _FOLD_RECORD.values():
        if other is not None:
            out.pop(other, None)
    if record is not None:
        # Copied as is, not coerced: a malformed record is check_decoder's to
        # refuse, by name, below.
        out[record] = copy.deepcopy(fold[record])
    out["validated"] = False
    out.pop("folds", None)
    out.pop("context_variants", None)
    dead_time_ml.check_decoder(out, where)
    return out


def learned_windows(game: dv.Game, weights: dict, *, gated: bool) -> list[Interval]:
    """The learned builder on `game`; on a declined track, production's
    fallback (`guarded`, which abstains there).

    `gated=False` is D-0034's rung: `min_track_rate=0.0`. That removes the
    builder's floor entirely, so the one decline it still makes is an empty
    position list; a track with points but no usable speed sample (one point, or
    points further apart than `MAX_SAMPLE_SPACING`) would be scored at its fills
    and cut everything. It is declined here instead, as the floor would.
    """
    if not gated and len(game.speed_samples[0]) == 0:
        return dv.v5_guarded(game)
    kwargs = {} if gated else {"min_track_rate": 0.0}
    try:
        return active_windows_learned(
            game.contacts, game.positions, game.duration, game.frame_height,
            weights=weights, allow_unvalidated=True, **kwargs,
        )
    except TrackTooSparseError:
        return dv.v5_guarded(game)


def score(game: dv.Game, windows: list[Interval]) -> Score:
    s = evaluate_deadtime(game.human_keep, windows, game.duration)
    return Score(s.dead_removed_pct or 0.0, s.live_removed_sec, net_seconds(s),
                 abstained=isinstance(windows, Abstained))


@dataclass
class GameResult:
    test_id: str
    usable_rate: float          # usable speed samples/s, track_is_usable's measure
    guarded: Score
    gated: Score
    ungated: Score
    insample: Score             # v6, the shipped weights
    windows: dict[str, list[Interval]]


def run(test_ids: Sequence[str] = TEST_IDS,
        weights_path: Path | None = None) -> list[GameResult]:
    weights = load_weights(weights_path or dead_time_ml.WEIGHTS_PATH, warn_unvalidated=False)
    folds = {f["held_out"]: f for f in weights["folds"]}
    out = []
    for t in test_ids:
        game = dv.load_game(t)
        fw = fold_weights(weights, folds[t])
        gated = learned_windows(game, fw, gated=True)
        ungated = learned_windows(game, fw, gated=False)
        out.append(GameResult(
            test_id=t,
            usable_rate=len(speed_samples(game.positions, game.frame_height)[0]) / game.duration,
            guarded=score(game, dv.v5_guarded(game)),
            gated=score(game, gated),
            ungated=score(game, ungated),
            insample=score(game, learned_windows(game, weights, gated=True)),
            windows={GATED: gated, UNGATED: ungated},
        ))
    return out


# ── report ─────────────────────────────────────────────────────────────────

def _cell(s: Score) -> str:
    if s.abstained:
        return f"{'abstains':>8s} {'':>6s} {s.net_sec:+8.1f}"
    return f"{s.dead_removed_pct * 100:7.1f}% {s.live_cut_sec:5.1f}s {s.net_sec:+8.1f}"


def print_report(results: Sequence[GameResult], weights: dict) -> bool:
    """Print the table and both rungs' clauses. Returns whether the gated rung
    — the one `"validated"` describes — passes."""
    folds = {f["held_out"]: f for f in weights["folds"]}
    decoder = dead_time_ml.decoder_of(weights)
    print(f"Held out (CF-393 folds), decoder {decoder}, contact scale "
          f"{vd.mirror_position()}, static-site filter {'on' if dv.STATIC_SITE_FILTER else 'off'}; net at "
          f"{vd.LIVE_CUT_COST:.0f}:1 live cut. Each cell: dead removed, live cut, net.\n")
    head = f"{'':>8s}{'dead':>8s} {'live':>6s} {'net':>8s}"
    print(f"{'fixture':8s} {'usable/s':>8s}  {'guarded':^24s}|{'learned gated':^24s}|"
          f"{'learned ungated':^24s}|{'in-sample (v6)':^24s}")
    print(f"{'':8s} {'':8s}  " + "|".join([head[8:]] * 4))
    for r in results:
        mark = "" if r.test_id in COMPARABLE else "  (excluded from totals)"
        print(f"{r.test_id:8s} {r.usable_rate:8.2f}  {_cell(r.guarded)}|{_cell(r.gated)}|"
              f"{_cell(r.ungated)}|{_cell(r.insample)}{mark}")

    def total(attr: str, field: str) -> float:
        return sum(getattr(getattr(r, attr), field) for r in results if r.test_id in COMPARABLE)

    print(f"{'comparable three':17s}  " + "|".join(
        f"{'':>8s} {total(a, 'live_cut_sec'):5.1f}s {total(a, 'net_sec'):+8.1f}"
        for a in ("guarded", "gated", "ungated", "insample")))

    # The trainer's record of the same folds, ungated by construction: a
    # mismatch here means this run's inputs are not the ones it was fitted on.
    print("\nCF-393 fold record (ungated): " + ", ".join(
        f"{t} {folds[t]['dead_removed_pct'] * 100:.1f}%/{folds[t]['live_cut_sec']:.1f}s/"
        f"{folds[t]['net_sec']:+.2f}" for t in (r.test_id for r in results)))

    by_id = {r.test_id: r for r in results}
    guarded = {t: r.guarded for t, r in by_id.items()}
    verdicts = {}
    for name, attr in (("gated (production's chain)", "gated"), ("ungated (D-0034)", "ungated")):
        clauses = apply_bar({t: getattr(r, attr) for t, r in by_id.items()}, guarded)
        verdicts[attr] = all(c.passed for c in clauses)
        print(f"\nbar, learned {name}: {'PASS' if verdicts[attr] else 'FAIL'}")
        for c in clauses:
            print(f"  {'pass' if c.passed else 'FAIL'}  {c.name:44s} value {c.value:8.2f}"
                  f"  limit {c.limit:8.2f}  margin {c.margin:+8.2f}  ({c.detail})")

    # The trainer chose the scored variant by clause 1's own quantity on these
    # games, so the headline clause 1 is selection-biased; show every variant's.
    variants = clause_1_by_variant(weights)
    if variants:
        print("\nclause 1 per recorded context variant (CF-393 fold record, ungated). The "
              "trainer chose the\nscored variant by this same summed held-out net, so the "
              "headline clause 1 is selection-biased:")
        for v in variants:
            c = v.clause
            print(f"  {'pass' if c.passed else 'FAIL'}  {v.variant:14s} value {c.value:8.2f}"
                  f"  limit {c.limit:8.2f}  margin {c.margin:+8.2f}"
                  f"{'  (chosen, scored above)' if v.chosen else ''}")
        if not all(v.clause.passed for v in variants):
            print("  -> clause 1 does not hold for every variant; a pass rests on the choice.")
    if decoder == "hmm":
        # The trainer's decoder choice is the same kind of selection, and its
        # losing folds are not recorded, so it can only be stated (CF-604).
        print("\ndecoder hmm was chosen over threshold on this same summed held-out net "
              "(train_deadtime.choose_decoder),\nso clause 1 is also biased by the "
              "decoder choice; the losing decoder's folds are not recorded.")
    print(f"\n\"validated\" in the weights file is {weights.get('validated')!r}; the bar "
          f"{'passes' if verdicts['gated'] else 'fails'} for the gated rung. This tool "
          "never writes the file.")
    return verdicts["gated"]


def main(argv: Sequence[str] | None = None) -> int:
    """Exit status 0 when the bar passes for the gated rung, 1 when it fails, so a
    script can gate on it. The report prints either way."""
    ap = argparse.ArgumentParser(description="CF-395: score the learned builder held out "
                                             "and apply the epic's validation bar")
    ap.add_argument("--dump-windows", type=Path, metavar="DIR",
                    help=f"also write each game's held-out keep-windows to "
                         f"DIR/{{test}}_{{{GATED}|{UNGATED}}}.json, for harness --windows-json")
    args = ap.parse_args(argv)

    # The position the folds were fitted at, set for this run and restored on
    # the way out, as visualize_deadtime.main does.
    saved = (dv.NORMALIZE, dv.SOFT_SCALE, dv.STATIC_SITE_FILTER)
    previous = logging.root.manager.disable
    try:
        vd.apply_contact_scale("on")
        dv.STATIC_SITE_FILTER = False
        logging.disable(logging.INFO)
        weights = load_weights(dead_time_ml.WEIGHTS_PATH, warn_unvalidated=False)
        results = run()
        passed = print_report(results, weights)
        if args.dump_windows:
            args.dump_windows.mkdir(parents=True, exist_ok=True)
            for r in results:
                for key, windows in r.windows.items():
                    vd.dump_windows(args.dump_windows, r.test_id, key, windows, "on", "off")
            print(f"\nDumped {GATED}/{UNGATED} keep-windows -> {args.dump_windows}")
    finally:
        dv.NORMALIZE, dv.SOFT_SCALE, dv.STATIC_SITE_FILTER = saved
        logging.disable(previous)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
