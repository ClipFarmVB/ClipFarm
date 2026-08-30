"""
Unit tests for the dead-time report formatting in ml/eval/harness.py.

The formatters decide what a human actually sees of a run — a truncation that
dropped seconds from the totals, or a comparison column that misaligns signs,
would misreport results while every metric underneath stays correct.
"""
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import harness, metrics
from ml.eval.harness import (
    _seconds_to_ts,
    format_deadtime_audit,
    format_deadtime_comparison,
    load_windows_json,
)
from ml.eval.metrics import evaluate_deadtime


class TestSecondsToTs:
    def test_mm_ss_under_an_hour(self):
        assert _seconds_to_ts(65) == "01:05"
        assert _seconds_to_ts(3599) == "59:59"

    def test_rolls_over_to_h_mm_ss(self):
        # The labels write 1:00:16, not 60:16 — the report must match.
        assert _seconds_to_ts(3600) == "1:00:00"
        assert _seconds_to_ts(3616) == "1:00:16"
        assert _seconds_to_ts(7325) == "2:02:05"


def _signals_with_overcut(n_spans: int):
    """n human rallies of distinct lengths, model keeps nothing → every rally
    lands in over_cut_live, longest first."""
    human = [(i * 100.0, i * 100.0 + 10.0 + i) for i in range(n_spans)]
    return evaluate_deadtime(human, [], duration=n_spans * 100.0 + 100.0)


class TestFormatDeadtimeAudit:
    def test_truncates_and_summarizes_the_rest(self):
        s = _signals_with_overcut(20)
        out = format_deadtime_audit(s, limit=3)
        assert "20 spans" in out
        # 3 shown + "+ 17 shorter" summary; the summary's seconds must equal
        # the hidden spans' seconds exactly, or the report misstates totals.
        assert "+ 17 shorter" in out
        hidden_sec = sum(b - a for a, b in s.over_cut_live[3:])
        assert f"{hidden_sec:.0f}s total" in out

    def test_limit_zero_prints_everything(self):
        s = _signals_with_overcut(20)
        out = format_deadtime_audit(s, limit=0)
        assert "shorter" not in out
        # every span appears
        assert out.count("s\n") + out.count("s ") >= 20

    def test_empty_lists_say_none(self):
        s = evaluate_deadtime([(0.0, 50.0)], [(0.0, 50.0)], duration=50.0)
        out = format_deadtime_audit(s)
        assert "OVER-CUT LIVE (real play removed - act on these): none" in out
        assert "MISSED DEAD (dead time kept): none" in out


class TestFormatDeadtimeComparison:
    def test_deltas_have_correct_sign_and_magnitude(self):
        human = [(0.0, 30.0)]
        left = evaluate_deadtime(human, [(0.0, 10.0)], duration=100.0)   # cut 20s live
        right = evaluate_deadtime(human, [(0.0, 20.0)], duration=100.0)  # cut 10s live
        out = format_deadtime_comparison("T", "left", left, "right", right)
        assert "-10s" in out          # live wrongly removed went down by 10
        assert "+33.3pp" in out       # recall 33.3% -> 66.7%
        assert "+10.0pp" in out       # condense ratio 10% -> 20%


class TestLoadWindowsJson:
    def test_reads_keep_key_and_bare_list(self, tmp_path):
        p = tmp_path / "w.json"
        p.write_text('{"keep": [{"start": "01:00", "end": 90}]}', encoding="utf-8")
        assert load_windows_json(p) == [(60.0, 90.0)]
        p.write_text('[{"start": 5, "end": 6}]', encoding="utf-8")
        assert load_windows_json(p) == [(5.0, 6.0)]

    def test_dict_without_keep_fails_with_clear_message(self, tmp_path):
        # Previously fell through to iterating the dict's keys → cryptic TypeError.
        p = tmp_path / "w.json"
        p.write_text('{"windows": []}', encoding="utf-8")
        with pytest.raises(SystemExit, match="keep"):
            load_windows_json(p)


def _floats(obj, path=""):
    """Every float in a nested result row, with its path."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _floats(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        yield path, obj


def _noisy_signals(empty: bool = False):
    """EvalSignals carrying a float tail on **every** field, so the rounding
    test can fail for any of them.

    This is a **worst case, not a replica of the baseline**, and the two ideas
    pull against each other. A field whose fixture value is already exact at the
    rounding threshold makes `test_every_float_in_a_result_row_is_rounded`
    vacuous for that field, silently — dropping `_round` from it survives the
    suite. `bridge` is `0.0` in the committed baseline and `duration` is
    `3660.0`, so faithful values there would do exactly that; `human_seconds` is
    `480.0` and would too.

    Restoring any of these to the baseline is the tidy-looking change that
    reopens the hole, so it is no longer left to this docstring to prevent:
    `test_the_fixtures_can_actually_fail_the_rounding_assertion` asserts the
    precondition for every float here, named or not.
    """
    return metrics.EvalSignals(
        captured_pct=None if empty else 0.18506944444444462,
        buckets=metrics.CaptureBuckets(well_captured=3, butchered=1, missed=2, total=6),
        # `total` is a property, not a field — it sums the four below, which is
        # exactly how the noisy tail in the committed baseline arises.
        incorrect=metrics.IncorrectTime(
            junk=59.99999999999943,
            lead_slop=5.000000000000014,
            tail_slop=11.833333333333414,
            # Interval-difference arithmetic, like junk and the two slops above.
            bridge=2.8333333333333712,
        ),
        auc=None if empty else 0.8312500000000003,
        human_seconds=415.16666666666663,
        model_seconds=302.3333333333335,
        per_clip=[],
        per_window=[],
    )


def _noisy_deadtime(empty: bool = False):
    return metrics.DeadTimeSignals(
        dead_removed_pct=None if empty else 0.6843121036669423,
        live_removed_sec=516.6666666666663,
        live_removed_pct=None if empty else 0.415995705850778,
        kept_play_pct=None if empty else 0.5840042941492221,
        condense_ratio=None if empty else 0.4067395264116576,
        human_keep_sec=1241.6666666666665,
        human_dead_sec=2418.333333333333,
        model_keep_sec=1488.666666666667,
        # `n_frames / fps` in a real run — at 29.97 fps an hour of video gives
        # 3660.6606606606606. (`duration` is a passthrough on DeadTimeSignals;
        # its sources are `video_duration_sec`, a `max(rally ends)` fallback, or
        # that division — the division is where the tail comes from.)
        duration=3660.000000000001,
        over_cut_live=[(1.23456789, 2.3456789)],
        missed_dead=[(10.111111111, 20.222222222)],
    )

# ── CF-94: result rows are rounded on write ─────────────────────────────────
#
# results/*.jsonl is committed so runs can be diffed across versions. Raw float
# tails defeat that — `59.99999999999943` against `60.00000000000012` is the
# same number twice and reads as a change.


# 4, spelled out rather than imported. A guard that reads RESULT_FLOAT_PLACES
# to decide what counts as noisy agrees with whatever value the constant takes,
# including one loosened far enough to put the float tails back into the
# committed results — which is the condition CF-94 exists to prevent. The guard
# has to be pinned to something the change under guard cannot move.
EXPECTED_RESULT_FLOAT_PLACES = 4


class TestResultRounding:
    def test_the_rounding_threshold_has_not_moved(self):
        """Nothing else fails if RESULT_FLOAT_PLACES is loosened. Control
        mutation: setting it to 12 leaves the whole suite green while the
        committed results go straight back to 12-place noise.
        `test_rounding_does_not_move_a_value_meaningfully` bounds the other
        direction, and only past 1e-3 — a drop to 3dp survives it too."""
        assert harness.RESULT_FLOAT_PLACES == EXPECTED_RESULT_FLOAT_PLACES

    def test_the_fixtures_can_actually_fail_the_rounding_assertion(self):
        """Guards the guard. A fixture float already exact at the threshold
        makes test_every_float_in_a_result_row_is_rounded vacuous for that
        field — which is the CF-94 review finding for `bridge` and `duration`,
        and what would happen to `human_seconds` if anyone restored it to the
        baseline's 480.0.

        Walks the dataclasses rather than the serialised rows: after
        serialisation every float is rounded by construction, so the question
        can only be asked of the pre-serialisation values."""
        for name, raw in (("signals", _noisy_signals()), ("deadtime", _noisy_deadtime())):
            for path, value in _floats(asdict(raw)):
                places = len(repr(value).split(".")[-1])
                assert places > EXPECTED_RESULT_FLOAT_PLACES, (
                    f"{name}{path} = {value!r} is already exact at "
                    f"{EXPECTED_RESULT_FLOAT_PLACES}dp — the rounding test "
                    f"cannot fail for this field"
                )

    def test_the_written_parts_sum_to_the_written_total(self):
        """CF-352. `IncorrectTime.total` sums the raw parts, so rounding it
        directly produced a row that did not add up — 79.6666 against a written
        79.6667. Nothing reads these files back, but they exist to be diffed by
        humans, and `_decompose_window` documents the decomposition as exact."""
        row = harness._signals_to_dict(_noisy_signals())["incorrect_seconds"]
        parts = [row["junk"], row["lead_slop"], row["tail_slop"], row["bridge"]]

        assert row["total"] == round(sum(parts), EXPECTED_RESULT_FLOAT_PLACES)
        # And the fixture actually exercises the disagreement: rounding the raw
        # total gives a different answer, so the assertion above would fail
        # against the old serialiser rather than passing for free. This half is
        # a fixture precondition like
        # test_the_fixtures_can_actually_fail_the_rounding_assertion, and fails
        # for the same reason — if bridge goes back to 0.0 the parts sum
        # cleanly, and if the threshold is loosened both sides agree again.
        assert row["total"] != harness._round(_noisy_signals().incorrect.total)

    def test_every_float_in_a_result_row_is_rounded(self):
        """Both serialisers, because there are two result files and the card
        named one. `test1_deadtime.jsonl` was as noisy as `test1.jsonl`."""
        signals = harness._signals_to_dict(_noisy_signals())
        dead = harness._deadtime_to_dict(_noisy_deadtime())

        for name, row in (("pre_gate", signals), ("deadtime", dead)):
            for path, value in _floats(row):
                places = len(repr(value).split(".")[-1])
                assert places <= harness.RESULT_FLOAT_PLACES, (
                    f"{name}{path} = {value!r} kept {places} decimal places"
                )

    def test_none_survives_rounding(self):
        """captured_pct and auc are None when undefined — no human clips, or no
        positive/negative windows to separate. `round(None, 4)` is a TypeError,
        so a rounder that forgets this crashes the run that records the result
        rather than the one that computes it."""
        assert harness._round(None) is None
        assert harness._signals_to_dict(_noisy_signals(empty=True))["captured_pct"] is None
        assert harness._signals_to_dict(_noisy_signals(empty=True))["auc"] is None
        assert harness._deadtime_to_dict(_noisy_deadtime(empty=True))["dead_removed_pct"] is None

    def test_counts_are_left_as_integers(self):
        """The buckets are counts, not measurements. A round() there would be a
        no-op that reads as a guard against something."""
        buckets = harness._signals_to_dict(_noisy_signals())["buckets"]

        assert all(isinstance(v, int) for v in buckets.values()), buckets

    def test_rounding_does_not_move_a_value_meaningfully(self):
        """The point is readability, not precision loss. 4dp is 0.01% on a
        ratio and 0.1 ms on a second — if this ever fails, the places constant
        has been dropped far enough to change what the metric says."""
        raw = _noisy_signals()
        row = harness._signals_to_dict(raw)

        assert abs(row["incorrect_seconds"]["total"] - raw.incorrect.total) < 1e-3
        assert abs(row["captured_pct"] - raw.captured_pct) < 1e-3
