"""
Unit tests for the dead-time report formatting in ml/eval/harness.py.

The formatters decide what a human actually sees of a run — a truncation that
dropped seconds from the totals, or a comparison column that misaligns signs,
would misreport results while every metric underneath stays correct.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
