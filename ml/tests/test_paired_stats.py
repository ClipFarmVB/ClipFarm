"""CF-604: McNemar and the within-game block bootstrap, `ml/eval/paired_stats.py`.

The McNemar values are checked against hand counts of the binomial tail, and
the plan's own worked example (10 vs 24 discordant, p ~= 0.024).
"""
import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="the bootstrap is numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import paired_stats as ps  # noqa: E402
from ml.eval import rally_match as rm  # noqa: E402
from ml.eval.metrics import evaluate_deadtime  # noqa: E402
from ml.eval.visualize_deadtime import net_seconds  # noqa: E402


def test_mcnemar_matches_the_plans_worked_example():
    assert ps.mcnemar_exact(10, 24) == pytest.approx(0.0243, abs=5e-4)
    assert ps.mcnemar_exact(24, 10) == ps.mcnemar_exact(10, 24)


def test_mcnemar_is_the_doubled_binomial_tail():
    # b=1, c=5: P(X<=1) for Bin(6, .5) = (1 + 6) / 64; doubled = 14/64.
    assert ps.mcnemar_exact(1, 5) == pytest.approx(14 / 64)
    # All one way, n=5: 2 * 1/32.
    assert ps.mcnemar_exact(0, 5) == pytest.approx(2 / 32)
    assert ps.mcnemar_exact(0, 5) == pytest.approx(2 * math.comb(5, 0) / 2 ** 5)


def test_mcnemar_caps_at_one_and_reads_no_pairs_as_no_evidence():
    assert ps.mcnemar_exact(3, 3) == 1.0
    assert ps.mcnemar_exact(0, 0) == 1.0
    with pytest.raises(ValueError):
        ps.mcnemar_exact(-1, 2)


def test_unit_scores_sum_to_the_games_own_net_and_live_cut():
    keep = [(10.0, 12.0), (20.0, 26.0), (40.0, 52.0)]
    windows = [(9.0, 11.5), (30.0, 45.0), (50.0, 58.0)]
    dur = 60.0
    rows = ps.unit_scores(keep, windows, rm.units(keep, dur))
    sig = evaluate_deadtime(keep, windows, dur)
    assert rows[:, 0].sum() == pytest.approx(net_seconds(sig))
    assert rows[:, 1].sum() == pytest.approx(sig.live_removed_sec)
    # The second unit (20-40) lost its whole 6s rally and kept 10s of its gap.
    assert rows[1].tolist() == pytest.approx([(20 - 6 - 10) - 4 * 6, 6])


def test_the_bootstrap_resamples_within_each_game_and_is_seeded():
    a = np.array([[1.0, 0.0], [3.0, 1.0]])
    b = np.array([[10.0, 2.0]])
    boot = ps.block_bootstrap([a, b], draws=500, seed=1)
    assert boot.shape == (500, 2)
    # Game b has one unit: every draw holds it once; game a's two draws sum to
    # 2, 4 or 6. A pooled resample could draw b twice and reach 20+.
    assert set(np.round(boot[:, 0], 6)) <= {12.0, 14.0, 16.0}
    assert np.array_equal(boot, ps.block_bootstrap([a, b], draws=500, seed=1))
    assert not np.array_equal(boot, ps.block_bootstrap([a, b], draws=500, seed=2))
    assert ps.block_bootstrap([np.zeros((0, 2)), b], draws=3).tolist() == [[10.0, 2.0]] * 3


def test_the_percentile_interval_is_central():
    lo, hi = ps.percentile_interval(np.arange(101, dtype=float))
    assert (lo, hi) == pytest.approx((2.5, 97.5))
