"""
CF-395: the epic's validation bar, the per-fold weights it is judged on, and the
gated / ungated learned rungs (D-0034).

The bar is held to the committed fold record here, which needs no ball caches,
so CI pins the verdict: if the weights file's `"validated"` stops matching what
the bar says about its own folds, a test below fails.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="the learned builder does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import deadtime_variants as dv  # noqa: E402
from ml.eval import validate_learned as vl  # noqa: E402
from ml.eval.validate_learned import Score, apply_bar, fold_weights, scores_from_folds  # noqa: E402
from ml.pipeline import dead_time as dt  # noqa: E402
from ml.pipeline.dead_time_ml import (  # noqa: E402
    FEATURE_NAMES,
    FEATURE_VERSION,
    SCALING,
    WEIGHTS_PATH,
    active_windows_learned,
    check_contract,
    context_rule,
    load_weights,
)

H = 360
DURATION = 120.0


# ── the bar ────────────────────────────────────────────────────────────────

def _guarded():
    """A guarded side with round numbers: 100s live and 300s net over the three."""
    g = {t: Score(0.5, 100.0 / 3, 100.0) for t in ("test2", "test4", "test5")}
    g["test1"] = Score(0.5, 10.0, 10.0)
    g["test3"] = Score(0.0, 0.0, 0.0, abstained=True)
    return g


def _learned(**over):
    """Learned == guarded, with per-game overrides of (live, net)."""
    out = dict(_guarded())
    for t, (live, net) in over.items():
        out[t] = Score(0.6, live, net)
    return out


def _by_name(clauses):
    return {c.name.split()[0] + (" " + c.name.split()[1] if c.name.startswith("3") else ""): c
            for c in clauses}


def test_the_bars_numbers_are_the_epics():
    """CF-389 decision 6, verbatim. Changing one is changing the bar."""
    assert vl.SUMMED_LIVE_SLACK_SEC == 15.0
    assert vl.PER_GAME_LIVE_SLACK_SEC == 30.0
    assert vl.TEST3_LIVE_CAP_SEC == 30.0
    assert vl.COMPARABLE == ("test2", "test4", "test5")
    assert vl.TEST3 == "test3"


def test_clause_1_is_strict():
    g = _guarded()
    tie = apply_bar(_learned(), g)
    assert not _by_name(tie)["1"].passed          # equal net does not exceed
    ahead = apply_bar(_learned(test2=(100.0 / 3, 100.01)), g)
    assert _by_name(ahead)["1"].passed
    assert _by_name(ahead)["1"].margin == pytest.approx(0.01)


def test_clause_2_allows_exactly_15s_more_summed_live():
    g = _guarded()
    at = apply_bar(_learned(test2=(100.0 / 3 + 15.0, 200.0)), g)
    assert _by_name(at)["2"].passed and _by_name(at)["2"].margin == pytest.approx(0.0)
    over = apply_bar(_learned(test2=(100.0 / 3 + 15.01, 200.0)), g)
    assert not _by_name(over)["2"].passed
    assert _by_name(over)["2"].margin == pytest.approx(-0.01)


@pytest.mark.parametrize("game", ["test2", "test4", "test5"])
def test_clause_3_allows_exactly_30s_more_on_each_game(game):
    g = _guarded()
    # Another game cuts less, so the sum (clause 2) stays inside its slack and
    # only the per-game clause is under test.
    other = next(t for t in ("test2", "test4", "test5") if t != game)
    at = apply_bar(_learned(**{game: (100.0 / 3 + 30.0, 100.0), other: (0.0, 100.0)}), g)
    assert _by_name(at)[f"3 {game}"].passed
    over = apply_bar(_learned(**{game: (100.0 / 3 + 30.01, 100.0), other: (0.0, 100.0)}), g)
    assert not _by_name(over)[f"3 {game}"].passed
    assert _by_name(over)["2"].passed


def test_clause_4_passes_an_abstain_whatever_its_live_cut():
    g = _guarded()
    learned = _learned()
    learned["test3"] = Score(0.0, 999.0, 0.0, abstained=True)
    assert _by_name(apply_bar(learned, g))["4"].passed


def test_clause_4_caps_a_condensed_test3_at_30s():
    g = _guarded()
    at, over = _learned(test3=(30.0, 50.0)), _learned(test3=(30.01, 50.0))
    assert _by_name(apply_bar(at, g))["4"].passed
    assert not _by_name(apply_bar(over, g))["4"].passed


def test_excluded_games_do_not_reach_clauses_1_to_3():
    g = _guarded()
    learned = _learned(test2=(100.0 / 3, 100.01), test1=(9999.0, -9999.0))
    clauses = apply_bar(learned, g)
    assert all(c.passed for c in clauses)


# ── the committed folds: the verdict CI pins ───────────────────────────────

@pytest.fixture
def committed():
    return json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))


def _gated_from_record(learned, guarded):
    """The fold record is ungated; production's chain differs only where the
    builder declines, which on the fixtures is test3, where guarded abstains."""
    gated = dict(learned)
    gated["test3"] = Score(guarded["test3"].dead_removed_pct, guarded["test3"].live_cut_sec,
                           guarded["test3"].net_sec, abstained=True)
    return gated


def test_the_committed_folds_fail_clauses_2_and_3_on_both_rungs(committed):
    learned, guarded = scores_from_folds(committed["folds"])
    for rung in (learned, _gated_from_record(learned, guarded)):
        verdict = {c.name: c.passed for c in apply_bar(rung, guarded)}
        failed = sorted(n for n, ok in verdict.items() if not ok)
        assert [n.split()[0] for n in failed] == ["2", "3"]
        assert any(n.startswith("3 test4") for n in failed)


def test_the_committed_margins(committed):
    """The figures the PR and README quote, from the committed record."""
    learned, guarded = scores_from_folds(committed["folds"])
    by = _by_name(apply_bar(learned, guarded))
    assert by["1"].margin == pytest.approx(4.92, abs=0.01)
    assert by["2"].margin == pytest.approx(-15.38, abs=0.01)
    assert by["3 test4"].margin == pytest.approx(-1.43, abs=0.01)
    assert by["4"].value == pytest.approx(14.5)


def test_validated_matches_the_bar(committed):
    """Ticket acceptance: `"validated"` in the weights file matches the bar.
    The gated rung is the one it describes — it is what production would ship."""
    learned, guarded = scores_from_folds(committed["folds"])
    passes = all(c.passed for c in apply_bar(_gated_from_record(learned, guarded), guarded))
    assert committed["validated"] is passes
    assert committed["validated"] is False


def test_clause_1_is_reported_for_every_recorded_variant(committed):
    """R1-M1: the trainer picks the variant by clause 1's own quantity on the
    held-out games, so the report must show clause 1 for the variant it did not
    pick. On the committed record the chosen one passes and the other fails."""
    by = {v.variant: v for v in vl.clause_1_by_variant(committed)}
    assert set(by) == set(committed["context_variants"])
    assert [n for n, v in by.items() if v.chosen] == ["none"]
    assert by["none"].clause.passed
    assert by["none"].clause.margin == pytest.approx(4.92, abs=0.01)
    assert not by["lags_1_3_5"].clause.passed
    assert by["lags_1_3_5"].clause.margin == pytest.approx(-23.58, abs=0.01)
    # The chosen variant's clause 1 is the headline clause 1.
    learned, guarded = scores_from_folds(committed["folds"])
    assert by["none"].clause == apply_bar(learned, guarded)[0]


def test_the_report_prints_every_variants_clause_1(committed, capsys):
    folds = {f["held_out"]: f for f in committed["folds"]}
    results = []
    for t, f in folds.items():
        s = Score(f["dead_removed_pct"], f["live_cut_sec"], f["net_sec"])
        g = Score(f["guarded_dead_removed_pct"], f["guarded_live_cut_sec"], f["guarded_net_sec"])
        results.append(vl.GameResult(t, 1.0, g, s, s, s, {}))
    vl.print_report(results, committed)
    out = capsys.readouterr().out
    assert "selection-biased" in out
    assert "lags_1_3_5" in out and "-23.58" in out
    assert "clause 1 does not hold for every variant" in out


def test_main_exits_non_zero_when_the_bar_fails(monkeypatch):
    """R1-N3: a failing bar is exit status 1, a passing one 0."""
    monkeypatch.setattr(vl, "run", lambda: [])
    for passed, status in ((False, 1), (True, 0)):
        monkeypatch.setattr(vl, "print_report", lambda r, w, p=passed: p)
        assert vl.main([]) == status


def test_every_fold_rebuilds_into_weights_the_builder_accepts(committed):
    for fold in committed["folds"]:
        fw = fold_weights(committed, fold)
        check_contract(fw)
        assert fw["coef"] == fold["coef"] and fw["threshold"] == fold["threshold"]


# ── per-fold weights ───────────────────────────────────────────────────────

def _weights(**over):
    coef = [0.0] * len(FEATURE_NAMES)
    coef[FEATURE_NAMES.index("mean_speed_3s")] = 4.0
    data = {
        "features": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION,
        "scaling": dict(SCALING), "context": context_rule([]),
        "coef": coef, "intercept": -2.0, "threshold": 0.5,
        "validated": True, "trained_on": "synthetic",
        "folds": [], "context_variants": {},
    }
    data.update(over)
    return data


def _fold(**over):
    data = {"held_out": "t", "threshold": 0.7, "coef": [1.0] * len(FEATURE_NAMES),
            "intercept": 0.25, "dead_removed_pct": 0.5, "live_cut_sec": 1.0,
            "net_sec": 2.0, "auc": 0.9, "guarded_dead_removed_pct": 0.4,
            "guarded_live_cut_sec": 1.0, "guarded_net_sec": 1.0}
    data.update(over)
    return data


def test_fold_weights_take_the_folds_model_and_keep_the_preparation():
    w = _weights()
    fw = fold_weights(w, _fold())
    assert fw["coef"] == [1.0] * len(FEATURE_NAMES)
    assert fw["intercept"] == 0.25 and fw["threshold"] == 0.7
    for k in ("features", "feature_version", "scaling", "context"):
        assert fw[k] == w[k]
    assert fw["validated"] is False
    assert "folds" not in fw and "context_variants" not in fw
    assert w["coef"][0] == 0.0 and w["validated"] is True     # input untouched


def test_fold_weights_carry_hmm_transitions_when_the_fold_has_them():
    fw = fold_weights(_weights(decoder="hmm"), _fold(hmm={"stay_in": 0.9}))
    assert fw["hmm"] == {"stay_in": 0.9}


@pytest.mark.parametrize("weights, fold, match", [
    (_weights(), _fold(extra=1), "unknown keys"),
    (_weights(decoder="beam"), _fold(), "decoder"),
    (_weights(), {k: v for k, v in _fold().items() if k != "threshold"}, "no threshold"),
    (_weights(decoder="hmm"), _fold(), "no hmm"),
])
def test_fold_weights_refuse_what_they_cannot_apply(weights, fold, match):
    with pytest.raises(ValueError, match=match):
        fold_weights(weights, fold)


# ── gated and ungated rungs ────────────────────────────────────────────────

def _track(step=0.33):
    """Dense track over the whole video, fast inside 10-30s. step=1.2 is
    below guarded's usable-sample floor."""
    out, t, x = [], 0.0, 0.0
    while t <= DURATION:
        out.append({"time": t, "x": x, "y": 180.0, "confidence": 0.9})
        x += (300.0 if 10.0 <= t <= 30.0 else 20.0) * step
        t += step
    return out


def _game(positions):
    return dv.Game(test_id="t", video_file="t.mp4", duration=DURATION, frame_height=H,
                   positions=positions, contacts=[], human_keep=[], raw={})


def test_on_a_sparse_track_gated_falls_back_and_ungated_judges():
    game = _game(_track(step=1.2))
    w = _weights(validated=False)
    gated = vl.learned_windows(game, w, gated=True)
    ungated = vl.learned_windows(game, w, gated=False)
    assert isinstance(gated, dt.Abstained) and gated == dv.v5_guarded(game)
    assert not isinstance(ungated, dt.Abstained)
    assert ungated == active_windows_learned([], game.positions, DURATION, H, weights=w,
                                             allow_unvalidated=True, min_track_rate=0.0)


def test_on_a_dense_track_the_two_rungs_agree():
    game = _game(_track())
    w = _weights(validated=False)
    assert vl.learned_windows(game, w, gated=True) == vl.learned_windows(game, w, gated=False)


def test_ungated_still_falls_back_on_an_empty_track():
    game = _game([])
    assert vl.learned_windows(game, _weights(), gated=False) == dv.v5_guarded(game)


def test_an_abstain_is_scored_as_one():
    game = _game(_track(step=1.2))
    assert vl.score(game, dv.v5_guarded(game)).abstained
    assert not vl.score(game, [(0.0, DURATION)]).abstained


# ── v7 on the ladder ───────────────────────────────────────────────────────

def test_v7_is_on_the_ladder_and_labelled_in_sample():
    label, fn = dv.VARIANTS["v7"]
    assert fn is dv.v7_learned_ungated
    assert "IN-SAMPLE" in label and "eval only" in label


def test_v7_is_v6_without_the_gate(monkeypatch, tmp_path):
    import ml.pipeline.dead_time_ml as dml

    p = tmp_path / "w.json"
    p.write_text(json.dumps(_weights(validated=False)))
    monkeypatch.setattr(dml, "WEIGHTS_PATH", p)
    sparse = _game(_track(step=1.2))
    assert isinstance(dv.v6_learned(sparse), dt.Abstained)
    assert not isinstance(dv.v7_learned_ungated(sparse), dt.Abstained)
    dense = _game(_track())
    assert dv.v7_learned_ungated(dense) == dv.v6_learned(dense)
    assert dv.v7_learned_ungated(_game([])) == dv.v5_guarded(_game([]))


# ── against the real caches, when present ──────────────────────────────────

_CACHES = dv.BALL_CACHE_DIR


def _have_caches() -> bool:
    try:
        weights = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        return all((_CACHES / f"{g['source_video_md5']}.json").exists()
                   for g in weights["trained_on"])
    except (OSError, KeyError, ValueError):
        return False


@pytest.mark.skipif(not _have_caches(), reason="ball caches are gitignored (CF-390)")
def test_the_ungated_rung_reproduces_the_trainers_fold_record():
    """The builder and the trainer score a fold the same way, so the ungated
    held-out rung must land on the record CF-393 committed. A mismatch means
    this module is not judging the model the file describes."""
    weights = load_weights(WEIGHTS_PATH, warn_unvalidated=False)
    saved = (dv.NORMALIZE, dv.SOFT_SCALE, dv.STATIC_SITE_FILTER)
    try:
        dv.NORMALIZE, dv.SOFT_SCALE, dv.STATIC_SITE_FILTER = True, False, False
        results = vl.run()
    finally:
        dv.NORMALIZE, dv.SOFT_SCALE, dv.STATIC_SITE_FILTER = saved
    folds = {f["held_out"]: f for f in weights["folds"]}
    for r in results:
        f = folds[r.test_id]
        assert r.ungated.live_cut_sec == pytest.approx(f["live_cut_sec"], abs=1e-6)
        assert r.ungated.net_sec == pytest.approx(f["net_sec"], abs=1e-5)
        assert r.guarded.live_cut_sec == pytest.approx(f["guarded_live_cut_sec"], abs=1e-5)
        # Gated differs only where the builder declines, and there it abstains.
        if r.gated.abstained:
            assert r.gated == r.guarded
        else:
            assert r.gated == r.ungated
