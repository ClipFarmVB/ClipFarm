"""CF-604: the per-rally scorer, `ml/eval/rally_match.py`.

Pure interval arithmetic, so every case is a hand-built timeline whose answer
can be counted on a finger: a rally is one span of union(human_keep).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import rally_match as rm  # noqa: E402

# Three rallies: 2s, 6s and 12s long, in a 60s video.
KEEP = [(10.0, 12.0), (20.0, 26.0), (40.0, 52.0)]
DUR = 60.0


def test_a_rally_is_one_span_of_the_labels_union():
    assert rm.rallies([(5.0, 8.0), (7.0, 9.0), (20.0, 21.0), (59.0, 70.0)], DUR) == [
        (5.0, 9.0), (20.0, 21.0), (59.0, 60.0)]


def test_keeping_everything_keeps_every_rally_whole():
    s = rm.summarize(rm.match_rallies(KEEP, [(0.0, DUR)], DUR))
    assert s["rallies"] == 3 and s["recall_50"] == 1.0 and s["recall_100"] == 1.0
    assert s["deleted"] == {"le_3s": 0, "3_8s": 0, "gt_8s": 0}


def test_whole_deletions_are_bucketed_by_the_rallys_length():
    # Nothing kept: one rally per bucket, each deleted whole.
    s = rm.summarize(rm.match_rallies(KEEP, [], DUR))
    assert s["deleted"] == {"le_3s": 1, "3_8s": 1, "gt_8s": 1}
    assert s["recall_50"] == 0.0 and s["recall_100"] == 0.0
    assert s["start_err_median_sec"] is None
    # The bucket edges are inclusive above: 3s and 8s fall in the lower bucket.
    assert [rm.bucket(x) for x in (3.0, 3.01, 8.0, 8.01)] == ["le_3s", "3_8s", "3_8s", "gt_8s"]


def test_recall_at_half_and_at_whole():
    # Rally 1 kept 1 of 2s (half), rally 2 kept 2 of 6s, rally 3 kept whole.
    windows = [(11.0, 13.0), (24.0, 26.0), (38.0, 53.0)]
    m = rm.match_rallies(KEEP, windows, DUR)
    assert [round(x.kept_frac, 6) for x in m] == [0.5, round(2 / 6, 6), 1.0]
    s = rm.summarize(m)
    assert s["recall_50"] == pytest.approx(2 / 3)
    assert s["recall_100"] == pytest.approx(1 / 3)
    assert s["deleted"] == {"le_3s": 0, "3_8s": 0, "gt_8s": 0}


def test_start_and_end_errors_are_signed():
    # Rally 3 (40-52): kept from 38 (2s lead-in) to 50 (2s of its end cut).
    m = rm.match_rallies([(40.0, 52.0)], [(38.0, 50.0)], DUR)[0]
    assert m.start_err == -2.0 and m.end_err == -2.0
    # Start cut by 1s, tail of 3s kept.
    m = rm.match_rallies([(40.0, 52.0)], [(41.0, 55.0)], DUR)[0]
    assert m.start_err == 1.0 and m.end_err == 3.0


def test_a_window_merged_across_rallies_is_read_inside_each_rallys_territory():
    # One window over all three rallies: each rally's lead-in is measured back
    # to the midpoint of the gap before it, not to the first rally's start.
    m = rm.match_rallies(KEEP, [(5.0, 55.0)], DUR)
    assert [x.start_err for x in m] == [-5.0, -4.0, -7.0]
    assert [x.end_err for x in m] == [4.0, 7.0, 3.0]


def test_a_stray_window_in_the_gap_does_not_save_a_deleted_rally():
    m = rm.match_rallies([(20.0, 26.0)], [(14.0, 18.0)], DUR)[0]
    assert m.deleted and m.start_err is None and m.end_err is None


def test_units_tile_the_video_one_per_rally():
    u = rm.units(KEEP, DUR)
    assert u == [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0)]
    assert rm.units([], DUR) == [(0.0, DUR)]


def test_no_rallies_summarizes_to_none_not_zero():
    s = rm.summarize([])
    assert s["rallies"] == 0 and s["recall_50"] is None and s["recall_100"] is None


def test_a_rally_kept_exactly_half_counts_at_50_whatever_its_placement():
    """R1-N1: recall_50 allows EPS like deleted / kept_whole, so float error
    in the kept seconds cannot drop an exactly-half rally below the line."""
    length = 3.3
    for i in range(1, 201):
        s = i / 10.0
        rally = [(s, s + length)]
        m = rm.match_rallies(rally, [(s, s + length / 2)], 250.0)
        assert rm.summarize(m)["recall_50"] == 1.0, s
