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
from ml.eval.metrics import evaluate_deadtime  # noqa: E402
from ml.pipeline import dead_time_ml as dml  # noqa: E402
from ml.pipeline.dead_time_ml import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402

IDS = ("test1", "test2", "test3", "test4", "test5")


def synthetic_game(test_id: str, n: int, seed: int) -> TD.TrainGame:
    """`n` seconds alternating 10s dead / 8s play, with two columns that know it."""
    rng = np.random.default_rng(seed)
    keep = [(float(s + 10), float(min(s + 18, n))) for s in range(0, n - 10, 18)]
    y = TD.labels(keep, n)
    feats = rng.normal(size=(n, len(FEATURE_NAMES)))
    feats[:, 0] += 2.0 * y
    feats[:, 2] += 1.0 * y
    return TD.TrainGame(
        test_id=test_id,
        source_video_md5=f"{seed:032x}",
        duration=float(n),
        human_keep=keep,
        features=feats,
        y=y,
        guarded=evaluate_deadtime(keep, [(0.0, float(n))], float(n)),
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
        "validated",
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
    per_fold = {"test1": 0.40, "test2": 0.90, "test3": 0.50, "test4": 0.60, "test5": 0.90}

    def stub(gs, model):
        (missing,) = set(IDS) - {g.test_id for g in gs}
        return per_fold[missing]

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

def _folds(nets: dict[str, float]) -> list[dict]:
    return [{"held_out": t, "net_sec": v} for t, v in nets.items()]


def test_the_variant_is_chosen_on_the_comparable_three_only():
    # "none" wins all five by a mile, carried entirely by test1 and test3;
    # "lags_1_3_5" wins test2/test4/test5. The comparable three decide.
    variants = {
        "none": _folds({"test1": 5000, "test2": 10, "test3": 5000, "test4": 10, "test5": 10}),
        "lags_1_3_5": _folds({"test1": -900, "test2": 20, "test3": -900, "test4": 20,
                              "test5": 20}),
    }
    assert TD.choose_variant(variants) == "lags_1_3_5"
    flipped = {
        "none": _folds({"test1": -900, "test2": 20, "test3": -900, "test4": 20, "test5": 20}),
        "lags_1_3_5": _folds({"test1": 5000, "test2": 10, "test3": 5000, "test4": 10,
                              "test5": 10}),
    }
    assert TD.choose_variant(flipped) == "none"


def test_a_tie_ships_the_first_listed_variant():
    same = _folds({"test1": 0, "test2": 5, "test3": 0, "test4": 5, "test5": 5})
    assert TD.choose_variant({"none": same, "lags_1_3_5": list(same)}) == "none"


def test_train_ships_the_variant_choose_variant_picks(games, fast_fit, monkeypatch):
    monkeypatch.setattr(TD, "choose_variant", lambda vf: "lags_1_3_5")
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


def test_hmm_folds_are_scored_through_the_shared_dispatch(games, monkeypatch, fast_fit):
    """The held-out windows come from `decode_windows` on the RAW trace with
    the fold's own transitions — the call the builder makes."""
    unsmoothed: list[np.ndarray] = []    # kept alive, so identity is meaningful
    calls: list[tuple[str, bool, dict]] = []
    real_decode, real_probs = TD.decode_windows, TD.probabilities

    def spy_probs(game, model, *, smooth=True):
        out = real_probs(game, model, smooth=smooth)
        if not smooth:
            unsmoothed.append(out)
        return out

    def spy_decode(raw, model, duration):
        calls.append((model["decoder"], any(raw is o for o in unsmoothed), model["hmm"]))
        return real_decode(raw, model, duration)

    monkeypatch.setattr(TD, "probabilities", spy_probs)
    monkeypatch.setattr(TD, "decode_windows", spy_decode)
    folds = TD.loo(games, (), "hmm")
    assert [c[0] for c in calls] == ["hmm"] * len(games)
    assert all(c[1] for c in calls), "a smoothed trace reached the hmm"
    assert [c[2] for c in calls] == [f["hmm"] for f in folds]


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
def test_choose_decoder_codes_the_shipping_rule(net_delta, live_delta, expected):
    thr = _scored(_BASE_NET, _BASE_LIVE)
    hmm = _scored({**_BASE_NET, "test4": 100.0 + net_delta},
                  {**_BASE_LIVE, "test4": 50.0 + live_delta})
    assert TD.choose_decoder(thr, hmm) == expected


def test_choose_decoder_reads_the_comparable_three_only():
    """hmm wins test1/test3 hugely and loses the comparable three: threshold."""
    thr = _scored(_BASE_NET, _BASE_LIVE)
    hmm = _scored({**_BASE_NET, "test1": 9000.0, "test3": 9000.0, "test2": 99.0},
                  {**_BASE_LIVE, "test1": 0.0, "test3": 0.0})
    assert TD.choose_decoder(thr, hmm) == "threshold"
    # And the converse: excluded fixtures cutting live play cannot block hmm.
    hmm = _scored({**_BASE_NET, "test2": 101.0},
                  {**_BASE_LIVE, "test1": 900.0, "test3": 900.0})
    assert TD.choose_decoder(thr, hmm) == "hmm"


@pytest.mark.parametrize("winner", ["threshold", "hmm"])
def test_main_writes_the_decoder_choose_decoder_picks(games, fast_fit, monkeypatch, winner):
    by_id = {g.test_id: g for g in games}
    written: list[dict] = []
    monkeypatch.setattr(TD.dv, "load_game", lambda t: by_id[t])
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "choose_decoder", lambda thr, hmm: winner)
    monkeypatch.setattr(TD, "write", lambda w: written.append(w))
    TD.main([])
    assert len(written) == 1 and written[0]["decoder"] == winner
    assert written[0] == TD.train(games, "abc1234", winner)[0]


def test_main_with_a_named_decoder_writes_that_one(games, fast_fit, monkeypatch):
    by_id = {g.test_id: g for g in games}
    written: list[dict] = []
    monkeypatch.setattr(TD.dv, "load_game", lambda t: by_id[t])
    monkeypatch.setattr(TD, "prepare", lambda g: g)
    monkeypatch.setattr(TD, "git_commit", lambda: "abc1234")
    monkeypatch.setattr(TD, "choose_decoder", lambda thr, hmm: "hmm")
    monkeypatch.setattr(TD, "write", lambda w: written.append(w))
    TD.main(["--decoder", "threshold"])
    assert [w["decoder"] for w in written] == ["threshold"]


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
