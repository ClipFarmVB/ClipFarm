"""CF-393 (#514): the leave-one-game-out trainer, `ml/eval/train_deadtime.py`.

CI has no ball caches, so every case runs on synthetic games built here — the
same approach as test_tune_contacts_loo.py. Each case is built to be able to
fail: the held-out game is spied on rather than assumed absent, the threshold
picker is stubbed to return a different value per fold so a mean or a mode
would not pass for the median, and the variant choice is given a variant that
wins only on the excluded fixtures.
"""
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="the trainer is numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import train_deadtime as TD  # noqa: E402
from ml.eval import deadtime_variants as dv  # noqa: E402
from ml.eval.metrics import DeadTimeSignals, evaluate_deadtime  # noqa: E402
from ml.pipeline import dead_time_ml as dml  # noqa: E402
from ml.pipeline.dead_time import Abstained  # noqa: E402
from ml.pipeline.dead_time_ml import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402

IDS = ("test1", "test2", "test3", "test4", "test5")


def synthetic_game(test_id: str, n: int, seed: int, *, declines: bool = False,
                   no_speed_samples: bool = False,
                   guarded_windows: list | None = None) -> TD.TrainGame:
    """`n` seconds alternating 10s dead / 8s play, with two columns that know it.
    `guarded` keeps everything unless told otherwise."""
    rng = np.random.default_rng(seed)
    keep = [(float(s + 10), float(min(s + 18, n))) for s in range(0, n - 10, 18)]
    y = TD.labels(keep, n)
    feats = rng.normal(size=(n, len(FEATURE_NAMES)))
    feats[:, 0] += 2.0 * y
    feats[:, 2] += 1.0 * y
    gw = [(0.0, float(n))] if guarded_windows is None else guarded_windows
    return TD.TrainGame(
        test_id=test_id,
        source_video_md5=f"{seed:032x}",
        duration=float(n),
        human_keep=keep,
        features=feats,
        y=y,
        guarded=evaluate_deadtime(keep, gw, float(n)),
        guarded_windows=gw,
        declines=declines,
        no_speed_samples=no_speed_samples,
    )


@pytest.fixture
def games():
    # Distinct lengths, so a row count identifies which games were stacked.
    return [synthetic_game(t, 60 + 7 * i, seed=i + 1) for i, t in enumerate(IDS)]


@pytest.fixture
def fast_fit(monkeypatch):
    """A short fit: the cases below test the plumbing, not convergence."""
    real = TD.fit_logistic
    monkeypatch.setattr(TD, "fit_logistic", lambda X, y: real(X, y, iters=50))


# ── determinism and the file's shape ───────────────────────────────────────

def test_two_runs_serialize_to_the_same_bytes(games, fast_fit):
    a, _ = TD.train(games, "abc1234")
    b, _ = TD.train(games, "abc1234")
    assert TD.serialize(a) == TD.serialize(b)
    assert "trained_at" not in a


def test_the_weights_keys_are_the_contract_in_order(games, fast_fit):
    weights, _ = TD.train(games, "abc1234")
    assert list(weights) == [
        "features", "feature_version", "scaling", "context", "coef", "intercept",
        "threshold", "decoder", "trained_on", "git_commit", "folds", "context_variants",
        "contact_scale", "nested", "validated",
    ]
    assert weights["decoder"] == "threshold"
    assert weights["validated"] is False
    assert weights["feature_version"] == FEATURE_VERSION
    assert weights["features"] == FEATURE_NAMES
    lags = weights["context"]["lags"]
    assert len(weights["coef"]) == len(FEATURE_NAMES) * (1 + len(lags))
    assert [f["held_out"] for f in weights["folds"]] == list(IDS)
    assert set(weights["context_variants"]) == set(TD.VARIANTS)
    assert weights["trained_on"][0] == {"test_id": "test1",
                                        "source_video_md5": games[0].source_video_md5}


def test_the_file_is_written_with_lf_endings(games, fast_fit, tmp_path):
    weights, _ = TD.train(games, "abc1234")
    p = tmp_path / "w.json"
    TD.write(weights, p)
    raw = p.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"}\n")
    assert json.loads(raw) == json.loads(TD.serialize(weights))


# ── leave-one-game-out never sees the held-out game ───────────────────────

def test_the_held_out_game_reaches_neither_the_fit_nor_the_threshold(games, monkeypatch):
    fitted: list[list[str]] = []
    picked: list[list[str]] = []
    stacked_rows: list[int] = []
    real_fit_on, real_pick, real_logistic = TD.fit_on, TD.pick_threshold, TD.fit_logistic

    def spy_fit_on(gs, *a, **k):
        fitted.append([g.test_id for g in gs])
        return real_fit_on(gs, *a, **k)

    def spy_pick(gs, model):
        picked.append([g.test_id for g in gs])
        return real_pick(gs, model)

    def spy_logistic(X, y):
        stacked_rows.append(X.shape[0])
        return real_logistic(X, y, iters=20)

    monkeypatch.setattr(TD, "fit_on", spy_fit_on)
    monkeypatch.setattr(TD, "pick_threshold", spy_pick)
    monkeypatch.setattr(TD, "fit_logistic", spy_logistic)

    folds = TD.loo(games, ())
    assert [f["held_out"] for f in folds] == list(IDS)
    assert len(fitted) == len(picked) == len(IDS)
    for held, fit_ids, pick_ids, rows in zip(games, fitted, picked, stacked_rows):
        others = [g.test_id for g in games if g is not held]
        assert fit_ids == others
        assert pick_ids == others
        # And nothing slipped held-out rows into the stacked matrix.
        assert rows == sum(g.features.shape[0] for g in games if g is not held)


def test_the_shipped_threshold_is_the_median_of_the_fold_thresholds(games, fast_fit,
                                                                    monkeypatch):
    # Keyed on the one game missing from the training set, so each fold gets a
    # different value; mean 0.66 and median 0.6 tell the two apart.
    # Nested selections pick on three-game sets; those get a constant.
    per_fold = {"test1": 0.40, "test2": 0.90, "test3": 0.50, "test4": 0.60, "test5": 0.90}

    def stub(gs, model):
        missing = set(IDS) - {g.test_id for g in gs}
        return per_fold[missing.pop()] if len(missing) == 1 else 0.35

    monkeypatch.setattr(TD, "pick_threshold", stub)
    weights, _ = TD.train(games, "x")
    assert [f["threshold"] for f in weights["folds"]] == [per_fold[t] for t in IDS]
    assert weights["threshold"] == 0.60


def test_pick_threshold_only_returns_a_listed_threshold(games, fast_fit):
    model = TD.fit_on(games[:2], ())
    assert TD.pick_threshold(games[:2], model) in TD.THRESHOLDS


def test_thresholds_are_clean_two_decimal_values():
    assert TD.THRESHOLDS[0] == 0.35 and TD.THRESHOLDS[-1] == 0.90
    assert all(t == round(t, 2) for t in TD.THRESHOLDS)
    assert len(TD.THRESHOLDS) == 12


# ── which variant ships ───────────────────────────────────────────────────

def _folds(nets: dict[str, float], live: dict[str, float] | None = None) -> list[dict]:
    live = live or {}
    return [{"held_out": t, "net_sec": v, "live_cut_sec": live.get(t, 0.0)}
            for t, v in nets.items()]


@pytest.fixture
def sized():
    """test1 ten times the length of the others, as the real test1 dwarfs them."""
    return [synthetic_game(t, 1000 if t == "test1" else 100, seed=i + 1)
            for i, t in enumerate(IDS)]


def test_the_objective_weighs_each_game_once_not_each_second(sized):
    # "none" wins test1 by 300s; "lags_1_3_5" wins each of the three 100s games
    # by 50s. Summed seconds would ship "none" (+300 vs +150); per game, test1's
    # +300 over 1000s is 0.3 against 3 x 0.5 = 1.5.
    variants = {
        "none": _folds({"test1": 400, "test2": 0, "test4": 0, "test5": 0}),
        "lags_1_3_5": _folds({"test1": 100, "test2": 50, "test4": 50, "test5": 50}),
    }
    assert TD.choose_variant(variants, sized) == "lags_1_3_5"
    # And test1 is not excluded: it decides when the rest tie.
    variants["lags_1_3_5"] = _folds({"test1": 100, "test2": 0, "test4": 0, "test5": 0})
    assert TD.choose_variant(variants, sized) == "none"


def test_test3_counts_only_through_the_abstain_clause(sized):
    # "none" is carried by test3's net alone: that never counts.
    variants = {
        "none": _folds({"test2": 10, "test3": 5000}),
        "lags_1_3_5": _folds({"test2": 20, "test3": -900}),
    }
    assert TD.choose_variant(variants, sized) == "lags_1_3_5"
    # Cutting more than the cap on test3 loses, whatever the other games say...
    over = _folds({"test2": 999, "test3": 0},
                  {"test3": TD.TEST3_LIVE_CAP_SEC + 0.5})
    under = _folds({"test2": 1, "test3": 0}, {"test3": TD.TEST3_LIVE_CAP_SEC})
    assert TD.choose_variant({"none": over, "lags_1_3_5": under}, sized) == "lags_1_3_5"
    # ...unless production's chain declines test3, which then abstains.
    declined = [synthetic_game(g.test_id, int(g.duration), seed=9, declines=g.test_id == "test3")
                for g in sized]
    assert TD.choose_variant({"none": over, "lags_1_3_5": under}, declined) == "none"


def test_a_tie_ships_the_first_listed_variant(sized):
    same = _folds({"test1": 0, "test2": 5, "test3": 0, "test4": 5, "test5": 5})
    assert TD.choose_variant({"none": same, "lags_1_3_5": list(same)}, sized) == "none"
    assert TD.choose_variant({"lags_1_3_5": same, "none": list(same)}, sized) == "lags_1_3_5"


def test_pick_threshold_uses_the_per_game_objective(sized, monkeypatch):
    """0.35 wins test1 by 300s, 0.90 wins the three short games by 50s each:
    raw summed seconds pick 0.35, the per-game objective 0.90."""
    def stub_score(game, raw, thr):
        if game.test_id == "test1":
            net = 400.0 if thr == 0.35 else 100.0
        elif game.test_id == "test3":
            net = 0.0
        else:
            net = 50.0 if thr == 0.90 else 0.0
        # All of it dead time removed, no live play cut: net_seconds == net.
        return DeadTimeSignals(
            dead_removed_pct=1.0, live_removed_sec=0.0, live_removed_pct=None,
            kept_play_pct=None, condense_ratio=None, human_keep_sec=0.0,
            human_dead_sec=net, model_keep_sec=0.0, duration=game.duration,
            missed_dead=[], over_cut_live=[])

    monkeypatch.setattr(TD, "score", stub_score)
    monkeypatch.setattr(TD, "probabilities", lambda g, m: None)
    assert TD.pick_threshold(sized, {}) == 0.90


def test_train_ships_the_variant_choose_variant_picks(games, fast_fit, monkeypatch):
    monkeypatch.setattr(TD, "choose_variant", lambda vf, gs: "lags_1_3_5")
    weights, report = TD.train(games, "x")
    assert report["chosen"] == "lags_1_3_5"
    assert weights["context"]["lags"] == list(TD.VARIANTS["lags_1_3_5"])
    assert weights["folds"] == weights["context_variants"]["lags_1_3_5"]["folds"]


# ── the decoder (CF-396) ──────────────────────────────────────────────────

def test_hmm_weights_carry_the_record_after_the_decoder(games, fast_fit):
    weights, _ = TD.train(games, "abc1234", "hmm")
    keys = list(weights)
    assert keys[keys.index("threshold"):keys.index("trained_on")] == [
        "threshold", "decoder", "hmm"]
    assert weights["decoder"] == "hmm"
    # Fitted on every game's labels, as the coefficients are.
    assert weights["hmm"] == dml.fit_transitions([g.y for g in games])
    dml.check_contract(weights)
    for f in weights["folds"]:
        assert set(f["hmm"]) == set(dml.HMM_KEYS)
        assert f["threshold"] in TD.THRESHOLDS, "hmm folds still pick a threshold"


def test_threshold_weights_and_folds_carry_no_hmm_record(games, fast_fit):
    """`--decoder threshold` must write what the trainer wrote before CF-396
    plus one key, so nothing hmm-shaped may leak into it."""
    weights, _ = TD.train(games, "abc1234", "threshold")
    assert "hmm" not in weights
    for variant in weights["context_variants"].values():
        assert all("hmm" not in f for f in variant["folds"])


def test_an_unknown_decoder_is_refused(games):
    with pytest.raises(ValueError, match="decoder"):
        TD.train(games, "x", "crf")


def test_hmm_transitions_are_fitted_on_the_training_games_only(games, monkeypatch, fast_fit):
    seen: list[int] = []
    real = TD.fit_transitions

    def spy(label_sets):
        seen.append(sum(len(y) for y in label_sets))
        return real(label_sets)

    monkeypatch.setattr(TD, "fit_transitions", spy)
    TD.loo(games, (), "hmm")
    assert seen == [sum(len(g.y) for g in games if g is not held) for held in games]


def test_probabilities_is_the_raw_trace(games, fast_fit):
    """`decode_windows` smooths for itself, so the trainer's traces must be
    the raw sigmoid, never a smoothed one."""
    model = TD.fit_on(games[:2], ())
    feats = games[2].features[:, TD._columns(model["columns"])]
    raw = dml.predict_in_play(feats, model, smooth=False)
    np.testing.assert_array_equal(TD.probabilities(games[2], model), raw)
    assert not np.allclose(raw, dml.predict_in_play(feats, model))


def test_hmm_folds_are_scored_through_the_shared_dispatch(games, monkeypatch, fast_fit):
    """The held-out windows come from `decode_windows` on the RAW trace with
    the fold's own transitions — the call the builder makes."""
    unsmoothed: list[np.ndarray] = []    # kept alive, so identity is meaningful
    calls: list[tuple[str, bool, dict]] = []
    real_decode, real_probs = TD.decode_windows, TD.probabilities

    def spy_probs(game, model):
        out = real_probs(game, model)
        unsmoothed.append(out)
        return out

    def spy_decode(raw, model, duration):
        # The threshold pick goes through the dispatch too; only the hmm
        # calls are the held-out scoring this test is about.
        if model["decoder"] == "hmm":
            calls.append((model["decoder"], any(raw is o for o in unsmoothed), model["hmm"]))
        return real_decode(raw, model, duration)

    monkeypatch.setattr(TD, "probabilities", spy_probs)
    monkeypatch.setattr(TD, "decode_windows", spy_decode)
    folds = TD.loo(games, (), "hmm")
    assert [c[0] for c in calls] == ["hmm"] * len(games)
    assert all(c[1] for c in calls), "a smoothed trace reached the hmm"
    assert [c[2] for c in calls] == [f["hmm"] for f in folds]


def test_threshold_folds_are_scored_through_the_shared_dispatch(games, monkeypatch, fast_fit):
    """Every threshold window the trainer scores — the pick over the training
    games and the held-out fold — is what `decode_windows` returns on the RAW
    trace: the call the builder makes. Scoring a smoothed trace, or thresholding
    without the dispatch, would tune and report windows the builder never ships."""
    assert TD.decode_windows is dml.decode_windows
    unsmoothed: list[np.ndarray] = []    # kept alive, so identity is meaningful
    decoded: list[tuple[bool, float, list]] = []
    scored: list[list] = []
    real_decode, real_probs, real_eval = TD.decode_windows, TD.probabilities, TD.evaluate_deadtime

    def spy_probs(game, model):
        out = real_probs(game, model)
        unsmoothed.append(out)
        return out

    def spy_decode(raw, model, duration):
        out = real_decode(raw, model, duration)
        decoded.append((any(raw is o for o in unsmoothed), model["threshold"], out))
        return out

    def spy_eval(keep, windows, duration):
        scored.append(windows)
        return real_eval(keep, windows, duration)

    monkeypatch.setattr(TD, "probabilities", spy_probs)
    monkeypatch.setattr(TD, "decode_windows", spy_decode)
    monkeypatch.setattr(TD, "evaluate_deadtime", spy_eval)
    held, train = games[1], [g for g in games if g is not games[1]]
    fold = TD.run_fold(held, train, ())

    assert len(scored) == len(TD.THRESHOLDS) * len(train) + 1
    assert len(decoded) == len(scored)
    for windows, (from_raw, _, out) in zip(scored, decoded):
        assert windows is out, "a threshold window was scored without the dispatch"
        assert from_raw, "a smoothed trace reached the threshold dispatch"
    assert [d[1] for d in decoded[:-1]] == [t for t in TD.THRESHOLDS for _ in train]
    assert decoded[-1][1] == fold["threshold"]

    # And the held-out windows are the builder's for these weights.
    model = TD.fit_on(train, ())
    raw = dml.predict_in_play(held.features, model, smooth=False)
    weights = {**model, "decoder": "threshold", "threshold": fold["threshold"]}
    assert scored[-1] == real_decode(raw, weights, held.duration)


def _scored(net: dict[str, float], live: dict[str, float]) -> list[dict]:
    return [{"held_out": t, "net_sec": net[t], "live_cut_sec": live[t]} for t in IDS]


_BASE_NET = {"test1": 100.0, "test2": 100.0, "test3": 100.0, "test4": 100.0, "test5": 100.0}
_BASE_LIVE = {"test1": 50.0, "test2": 50.0, "test3": 50.0, "test4": 50.0, "test5": 50.0}


@pytest.mark.parametrize("net_delta, live_delta, expected", [
    (+1.0, -1.0, "hmm"),          # net up, live down
    (+1.0, 0.0, "hmm"),           # net up, live unchanged: "does not rise"
    (+1.0, +0.5, "threshold"),    # net up, live up
    (0.0, -5.0, "threshold"),     # net tied: not an improvement
    (-1.0, -5.0, "threshold"),    # net down, live down
    (-1.0, +1.0, "threshold"),    # both worse
])
def test_choose_decoder_codes_the_shipping_rule(net_delta, live_delta, expected, games):
    thr = _scored(_BASE_NET, _BASE_LIVE)
    hmm = _scored({**_BASE_NET, "test4": 100.0 + net_delta},
                  {**_BASE_LIVE, "test4": 50.0 + live_delta})
    # test3 under the cap on both sides, so the seconds decide.
    thr[2]["live_cut_sec"] = hmm[2]["live_cut_sec"] = 0.0
    assert TD.choose_decoder(thr, hmm, games) == expected


def test_choose_decoder_weighs_games_and_reads_test3_through_its_clause(sized):
    """hmm wins test3's net hugely and loses elsewhere: threshold. test3 cutting
    over the cap blocks hmm whatever its net; test1 counts, once."""
    calm = {t: 0.0 for t in IDS}
    thr = _scored(_BASE_NET, calm)
    hmm = _scored({**_BASE_NET, "test3": 9000.0, "test2": 99.0}, calm)
    assert TD.choose_decoder(thr, hmm, sized) == "threshold"
    hmm = _scored({**_BASE_NET, "test2": 101.0}, {**calm, "test3": 900.0})
    assert TD.choose_decoder(thr, hmm, sized) == "threshold"
    assert TD.choose_decoder(hmm, thr, sized) == "hmm", "the clause, not the slot, decides"
    # +150s on 1000s of test1 is 0.15; -20s on each 100s game is -0.6.
    hmm = _scored({**_BASE_NET, "test1": 250.0, "test2": 80.0, "test4": 80.0,
                   "test5": 80.0}, calm)
    assert TD.choose_decoder(thr, hmm, sized) == "threshold"


@pytest.mark.parametrize("winner", ["threshold", "hmm"])
def test_main_writes_the_decoder_choose_decoder_picks(games, fast_fit, monkeypatch, winner):
    by_id = {g.test_id: g for g in games}
    written: list[dict] = []
    monkeypatch.setattr(TD.dv, "load_game", lambda t: by_id[t])
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "choose_decoder", lambda thr, hmm, gs: winner)
    monkeypatch.setattr(TD, "write", lambda w: written.append(w))
    TD.main([])
    assert len(written) == 1 and written[0]["decoder"] == winner
    assert written[0] == TD.train(games, "abc1234", "auto")[0]
    assert {f["decoder"] for f in written[0]["nested"]["folds"]} == {winner}


@pytest.mark.parametrize("variant", list(TD.VARIANTS))
def test_main_hands_choose_decoder_each_decoders_shipped_variant_folds(
        games, fast_fit, monkeypatch, variant):
    """The shipping rule is only as good as its inputs: each decoder's own
    held-out folds for the variant it ships — not the other variant's, nor
    one decoder's twice. The variant is forced, once per name, so a main that
    pinned either variant's folds would hand over the wrong ones here. Only the
    shipped selection's call is checked — the one over all five games; the
    nested selections make their own on four."""
    by_id = {g.test_id: g for g in games}
    seen: list[tuple] = []
    real_choose = TD.choose_decoder
    monkeypatch.setattr(TD.dv, "load_game", lambda t: by_id[t])
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "choose_variant", lambda vf, gs: variant)

    def spy(thr, hmm, gs):
        if len(gs) == len(IDS):
            seen.append((thr, hmm))
        return real_choose(thr, hmm, gs)

    monkeypatch.setattr(TD, "choose_decoder", spy)
    monkeypatch.setattr(TD, "write", lambda w: None)
    TD.main([])

    expected = {d: TD.select(games, (d,)).folds[d] for d in TD.DECODERS}
    assert len(seen) == 1
    thr, hmm = seen[0]
    assert thr == expected["threshold"][variant]
    assert hmm == expected["hmm"][variant]
    # The case can fail: every other candidate differs from the right one.
    candidates = [vf for d in TD.DECODERS for vf in expected[d].values()]
    assert sum(c == thr for c in candidates) == 1
    assert sum(c == hmm for c in candidates) == 1


def test_main_with_a_named_decoder_writes_that_one(games, fast_fit, monkeypatch):
    by_id = {g.test_id: g for g in games}
    written: list[dict] = []
    monkeypatch.setattr(TD.dv, "load_game", lambda t: by_id[t])
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "choose_decoder", lambda thr, hmm, gs: "hmm")
    monkeypatch.setattr(TD, "write", lambda w: written.append(w))
    TD.main(["--decoder", "threshold"])
    assert [w["decoder"] for w in written] == ["threshold"]
    assert {f["decoder"] for f in written[0]["nested"]["folds"]} == {"threshold"}


# ── AUC ───────────────────────────────────────────────────────────────────

def test_auc_matches_a_hand_count_without_ties():
    # pos {0.35, 0.8} vs neg {0.1, 0.4}: 0.35 beats 0.1 only, 0.8 beats both → 3/4
    assert TD.auc(np.array([0.1, 0.4, 0.35, 0.8]), np.array([0, 0, 1, 1])) == 0.75


def test_auc_counts_a_tie_as_half():
    # pos 0.5 vs neg {0.5, 0.5}: two ties = 1.0; pos 0.9 beats both = 2.0 → 3/4
    assert TD.auc(np.array([0.5, 0.5, 0.5, 0.9]), np.array([0, 1, 0, 1])) == 0.75
    # every score tied → blind
    assert TD.auc(np.full(6, 0.3), np.array([0, 1, 0, 1, 1, 0])) == 0.5


def test_auc_is_perfect_and_inverted_at_the_extremes():
    y = np.array([0, 0, 1, 1])
    assert TD.auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 1.0
    assert TD.auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 0.0


def test_auc_is_none_with_one_class():
    assert TD.auc(np.array([0.1, 0.2]), np.array([1, 1])) is None
    assert TD.auc(np.array([0.1, 0.2]), np.array([0, 0])) is None


# ── labels and the ablation table ─────────────────────────────────────────

def test_a_second_is_in_play_when_its_centre_is():
    # (2.0, 4.6): centres 2.5, 3.5, 4.5 in; 1.5 and 5.5 out. Overlaps union.
    y = TD.labels([(2.0, 4.6), (3.0, 4.0)], 7)
    assert y.tolist() == [0, 0, 1, 1, 1, 0, 0]


def test_the_ablation_subsets_are_the_epics():
    base = set(TD.ABLATION["base"])
    assert base == set(FEATURE_NAMES) - {"mean_conf_3s", "max_conf_3s", "mean_y_5s",
                                         "y_std_5s"}
    assert set(TD.ABLATION["+ confidence"]) - base == {"mean_conf_3s", "max_conf_3s"}
    assert set(TD.ABLATION["+ mean height"]) - base == {"mean_y_5s"}
    assert list(TD.ABLATION["all"]) == FEATURE_NAMES
    assert set(TD.ABLATION) == set(TD.EPIC_AUC)


def test_the_ablation_fits_on_the_subset_it_names(games, monkeypatch):
    widths: list[int] = []
    real = TD.fit_logistic

    def spy(X, y):
        widths.append(X.shape[1])
        return real(X, y, iters=5)

    monkeypatch.setattr(TD, "fit_logistic", spy)
    table = TD.ablation(games, (-1, 1))
    assert set(table) == set(TD.ABLATION)
    expected = [len(cols) * 3 for cols in TD.ABLATION.values() for _ in games]
    assert widths == expected


def test_the_exchange_rate_and_comparable_set_are_imported_not_copied():
    from ml.eval import visualize_deadtime as VD

    assert TD.LIVE_CUT_COST is VD.LIVE_CUT_COST
    assert TD.net_seconds is VD.net_seconds
    assert TD.COMPARABLE == ("test2", "test4", "test5")


# ── nested leave-one-game-out (CF-604) ────────────────────────────────────

def _canned_loo(table: dict[tuple[int, ...], dict[str, float]]):
    """A `loo` stand-in: each game's held-out net comes from `table[lags]`."""
    def loo(gs, lags, decoder="threshold"):
        return [{"held_out": g.test_id, "threshold": 0.5, "net_sec": table[tuple(lags)][g.test_id],
                 "live_cut_sec": 0.0} for g in gs]
    return loo


def test_the_outer_game_cannot_steer_its_own_folds_choice(games, fast_fit, monkeypatch):
    """"none" is carried by test2 alone, "lags_1_3_5" wins every other game.
    Chosen on all five — test2's own score included — "none" would win test2's
    fold; chosen on the four test2 leaves, "lags_1_3_5" must. Every other
    fold's training set holds test2, so "none" wins there."""
    lags = TD.VARIANTS["lags_1_3_5"]
    table = {(): {t: (1000.0 if t == "test2" else 0.0) for t in IDS},
             tuple(lags): {t: (0.0 if t == "test2" else 10.0) for t in IDS}}
    monkeypatch.setattr(TD, "loo", _canned_loo(table))
    assert TD.select(games, ("threshold",)).variant == "none", "the biased choice"
    record = TD.nested(games, ("threshold",), draws=0)
    assert {f["held_out"]: f["variant"] for f in record["folds"]} == {
        "test1": "none", "test2": "lags_1_3_5", "test3": "none", "test4": "none",
        "test5": "none"}
    # And the model scored is the variant chosen: lags widen the coefficients.
    by_id = {f["held_out"]: f for f in record["folds"]}
    assert len(by_id["test2"]["coef"]) == len(FEATURE_NAMES) * (1 + len(lags))
    assert len(by_id["test4"]["coef"]) == len(FEATURE_NAMES)


def test_no_choice_or_fit_in_a_nested_fold_sees_its_outer_game(games, fast_fit, monkeypatch):
    held_now: list[str] = []
    leaks: list[tuple[str, str]] = []
    real_nested_fold, real_select = TD.nested_fold, TD.select
    real_fit_on, real_pick, real_trans = TD.fit_on, TD.pick_threshold, TD.fit_transitions

    def watch(name, gs):
        if held_now and held_now[-1] in {g.test_id for g in gs}:
            leaks.append((held_now[-1], name))

    def spy_fold(held, train, decoders):
        held_now.append(held.test_id)
        try:
            return real_nested_fold(held, train, decoders)
        finally:
            held_now.pop()

    def spy_select(gs, decoders):
        watch("select", gs)
        return real_select(gs, decoders)

    def spy_fit_on(gs, *a, **k):
        watch("fit_on", gs)
        return real_fit_on(gs, *a, **k)

    def spy_pick(gs, model):
        watch("pick_threshold", gs)
        return real_pick(gs, model)

    monkeypatch.setattr(TD, "nested_fold", spy_fold)
    monkeypatch.setattr(TD, "select", spy_select)
    monkeypatch.setattr(TD, "fit_on", spy_fit_on)
    monkeypatch.setattr(TD, "pick_threshold", spy_pick)
    seen_labels: list[int] = []
    monkeypatch.setattr(TD, "fit_transitions",
                        lambda ys: seen_labels.append(sum(len(y) for y in ys)) or real_trans(ys))
    record = TD.nested(games, TD.DECODERS, draws=0)
    assert [f["held_out"] for f in record["folds"]] == list(IDS)
    assert leaks == []
    assert seen_labels, "the hmm path ran"


def test_a_nested_fold_scores_the_model_train_would_ship_from_its_four(games, fast_fit):
    """The outer fold's model is exactly the shipping procedure run on the
    other four: same decoder, variant, threshold and coefficients."""
    held, rest = games[1], [g for g in games if g is not games[1]]
    fold, _ = TD.nested_fold(held, rest, TD.DECODERS)
    shipped, _ = TD.train(rest, "x", "auto")
    assert fold["decoder"] == shipped["decoder"]
    assert list(TD.VARIANTS[fold["variant"]]) == shipped["context"]["lags"]
    assert fold["threshold"] == shipped["threshold"]
    assert fold["coef"] == shipped["coef"] and fold["intercept"] == shipped["intercept"]
    assert fold.get("hmm") == shipped.get("hmm")
    assert shipped["nested"]["bar"] is None, "four games do not cover the bar"


def test_nested_folds_carry_the_per_rally_and_paired_fields(games, fast_fit):
    record = TD.nested(games, ("threshold",), draws=50)
    for f in record["folds"]:
        for side in ("learned", "guarded"):
            r = f["rally"][side]
            assert r["rallies"] == len(TD.union(next(g for g in games
                                                     if g.test_id == f["held_out"]).human_keep))
            assert set(r["deleted"]) == {"le_3s", "3_8s", "gt_8s"}
            assert 0.0 <= r["recall_100"] <= r["recall_50"] <= 1.0
        p = f["paired"]
        # Unit deltas sum to the fold's own seconds: one scorer, not two.
        assert p["delta_net_sec"] == pytest.approx(f["net_sec"] - f["guarded_net_sec"], abs=1e-5)
        assert p["delta_live_cut_sec"] == pytest.approx(
            f["live_cut_sec"] - f["guarded_live_cut_sec"], abs=1e-5)
    pooled = record["paired"]
    comp = [f for f in record["folds"] if f["held_out"] in TD.COMPARABLE]
    assert pooled["games"] == list(TD.COMPARABLE)
    assert pooled["learned_only_deleted"] == sum(f["paired"]["learned_only_deleted"] for f in comp)
    assert pooled["guarded_only_deleted"] == sum(f["paired"]["guarded_only_deleted"] for f in comp)
    lo, hi = pooled["delta_net_ci95"]
    assert lo <= hi and pooled["bootstrap"]["descriptive"] is True
    assert "delta_net_ci95" not in TD.nested(games, ("threshold",), draws=0)["paired"]


def test_rally_record_counts_deletions_by_who_made_them():
    # Rallies at 10-18, 28-36, 46-54, 64-72 (n=80). guarded keeps all but the
    # third; learned keeps only the first and third.
    g = synthetic_game("test2", 80, 1, guarded_windows=[(0.0, 40.0), (60.0, 80.0)])
    rally, paired, deltas = TD.rally_record(g, [(9.0, 19.0), (45.0, 55.0)])
    assert (paired["learned_only_deleted"], paired["guarded_only_deleted"],
            paired["both_deleted"]) == (2, 1, 0)
    assert paired["mcnemar_p"] == 1.0
    assert rally["learned"]["deleted"] == {"le_3s": 0, "3_8s": 2, "gt_8s": 0}
    assert rally["guarded"]["recall_100"] == 0.75
    assert deltas.shape == (4, 2)


def test_the_gated_bar_puts_guarded_in_place_of_a_declined_game():
    """test3's nested fold cuts 40s, over the cap. Ungated that fails clause 4;
    gated, production's chain declines the track and guarded abstains."""
    gs = [synthetic_game(t, 100, i, declines=t == "test3",
                         guarded_windows=Abstained([(0.0, 100.0)]) if t == "test3" else None)
          for i, t in enumerate(IDS)]
    folds = [{"held_out": t, "dead_removed_pct": 0.5, "live_cut_sec": 40.0 if t == "test3" else 1.0,
              "net_sec": 10.0, "guarded_dead_removed_pct": 0.0, "guarded_live_cut_sec": 0.0,
              "guarded_net_sec": 0.0} for t in IDS]
    bar = TD.nested_bar(folds, gs)
    c4 = {rung: bar[rung]["clauses"][-1] for rung in ("gated", "ungated")}
    assert c4["ungated"]["passed"] is False
    assert c4["gated"]["passed"] is True and c4["gated"]["value"] == 0.0
    assert bar["gated"]["passed"] is True
    assert TD.nested_bar(folds[:4], gs) is None


def test_the_ungated_bar_declines_a_track_with_no_speed_sample_like_validate_learned():
    """R1-M2: validate_learned.learned_windows(gated=False) scores `guarded`
    on a track with no usable speed sample; the nested ungated rung matches.
    test3 here has none and its fold cuts 40s, over the cap: substituted, both
    rungs pass clause 4. A test3 with samples (below) still fails ungated."""
    def bar_for(no_samples: bool) -> dict:
        gs = [synthetic_game(t, 100, i, declines=t == "test3",
                             no_speed_samples=no_samples and t == "test3",
                             guarded_windows=Abstained([(0.0, 100.0)]) if t == "test3" else None)
              for i, t in enumerate(IDS)]
        folds = [{"held_out": t, "dead_removed_pct": 0.5,
                  "live_cut_sec": 40.0 if t == "test3" else 1.0, "net_sec": 10.0,
                  "guarded_dead_removed_pct": 0.0, "guarded_live_cut_sec": 0.0,
                  "guarded_net_sec": 0.0} for t in IDS]
        return TD.nested_bar(folds, gs)
    c4 = bar_for(True)["ungated"]["clauses"][-1]
    assert c4["passed"] is True and c4["value"] == 0.0
    assert bar_for(False)["ungated"]["clauses"][-1]["passed"] is False


def test_prepare_records_no_speed_samples_as_validate_learned_reads_them():
    """R1-M2: the flag is validate_learned's own test, len(speed_samples[0]) == 0,
    and it implies `declines`."""
    keep = [(1.0, 2.0)]
    raw = {"source_video_md5": "0" * 32}
    point = {"time": 5.0, "x": 10.0, "y": 10.0, "confidence": 1.0}
    one_point = dv.Game("t", "t.mp4", 10.0, 360, [point], [], keep, raw)
    assert len(one_point.speed_samples[0]) == 0
    g = TD.prepare(one_point)
    assert g.no_speed_samples and g.declines
    assert TD.prepare(dv.Game("t", "t.mp4", 10.0, 360, [], [], keep, raw)).no_speed_samples


def test_the_plain_folds_stay_readable_by_validate_learned(games, fast_fit):
    """`"folds"` keeps the shape validate_learned knows; the new fields live
    only under `"nested"`."""
    from ml.eval import validate_learned as vl

    weights, _ = TD.train(games, "x")
    for f in weights["folds"]:
        assert set(f) <= vl._FOLD_KEYS
        vl.fold_weights(weights, f)
    assert {"rally", "paired", "variant", "decoder"} <= set(weights["nested"]["folds"][0])


# ── the contact-scale position ────────────────────────────────────────────

@pytest.mark.parametrize("position", ["off", "on"])
def test_main_loads_at_the_position_asked_records_it_and_restores(games, fast_fit,
                                                                   monkeypatch, position):
    by_id = {g.test_id: g for g in games}
    loaded_at: list[tuple[bool, bool]] = []
    written: list[dict] = []
    before = (dv.NORMALIZE, dv.SOFT_SCALE)

    def load(t):
        loaded_at.append((dv.NORMALIZE, dv.SOFT_SCALE))
        return by_id[t]

    monkeypatch.setattr(TD.dv, "load_game", load)
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "write", lambda w: written.append(w))
    TD.main(["--decoder", "threshold", "--contact-scale", position])
    assert loaded_at == [(position == "on", False)] * len(IDS)
    assert written[0]["contact_scale"] == position
    assert (dv.NORMALIZE, dv.SOFT_SCALE) == before


def test_train_records_the_mirror_position_by_default(games, fast_fit):
    from ml.eval import visualize_deadtime as VD

    assert TD.train(games, "x")[0]["contact_scale"] == VD.mirror_position()


# ── label-jitter sensitivity ──────────────────────────────────────────────

def test_jitter_moves_every_endpoint_within_a_second_and_is_seeded():
    keep = [(0.5, 10.0), (20.0, 30.0), (95.0, 99.5)]
    a = TD.jitter_keep(keep, 100.0, np.random.default_rng(3))
    b = TD.jitter_keep(keep, 100.0, np.random.default_rng(3))
    assert a == b and a != keep
    for (s, e), (js, je) in zip(keep, a):
        assert abs(js - s) <= 1.0 and abs(je - e) <= 1.0
        assert 0.0 <= js < je <= 100.0
    # A span the draws empty is dropped, not inverted.
    assert TD.jitter_keep([(5.0, 5.1)], 10.0, np.random.default_rng(0), amount=0.0) == [(5.0, 5.1)]
    assert all(e > s for s, e in TD.jitter_keep([(5.0, 5.01)] * 50, 10.0,
                                                np.random.default_rng(1)))


def test_relabel_moves_the_labels_and_guarded_score_but_not_the_features(games):
    g = games[1]
    keep = [(0.0, 30.0)]
    r = TD.relabel(g, keep)
    assert r.human_keep == keep and r.y.sum() == 30
    assert r.features is g.features and r.guarded_windows is g.guarded_windows
    assert r.guarded.human_keep_sec == 30.0


def test_sensitivity_counts_the_draws_that_flip_the_ranking(games, monkeypatch):
    deltas = iter([+5.0, -3.0, +1.0, -0.5])

    def stub(gs, decoders, draws=0):
        d = next(deltas)
        return {"paired": {"delta_net_sec": d, "learned_only_deleted": 1,
                           "guarded_only_deleted": 2 if d > 0 else 0}}

    monkeypatch.setattr(TD, "nested", stub)
    baseline = {"paired": {"delta_net_sec": 4.0, "learned_only_deleted": 1,
                           "guarded_only_deleted": 2}}
    s = TD.sensitivity(games, ("threshold",), baseline, draws=4, seed=7)
    assert s["net_flips"] == 2 and s["deletion_flips"] == 2
    assert [r["delta_net_sec"] for r in s["runs"]] == [5.0, -3.0, 1.0, -0.5]
