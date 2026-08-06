"""
Integrity checks for the checked-in ground-truth fixtures.

Everything else in the eval suite runs on synthetic intervals, so nothing
guards the real files. A fixture is hand-authored data that silently decides
every score the harness reports — a malformed span or a drifted tier set would
show up as a plausible-looking number rather than an error.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval.harness import load_deadtime_fixture, load_fixture, parse_timestamp


class TestTest1DeadtimeFixture:
    def test_loads_and_spans_are_well_formed(self):
        fx = load_deadtime_fixture("test1")
        assert fx.duration == 3660.0
        assert fx.keep, "fixture yielded no in-play spans"
        prev_end = 0.0
        for start, end in fx.keep:
            assert start < end, f"non-positive span at {start}"
            assert 0.0 <= start and end <= fx.duration, f"span {start}-{end} outside video"
            assert start >= prev_end, f"span at {start} overlaps the previous one"
            prev_end = end

    def test_keep_tiers_filter_is_applied(self):
        """B (break) and O (outlier) spans must fall through to dead time."""
        fx = load_deadtime_fixture("test1")
        assert fx.raw["keep_tiers"] == ["M", "C", "N"]
        loaded = {(parse_timestamp(s["start"]), parse_timestamp(s["end"])) for s in fx.raw["spans"]}
        excluded = {
            (parse_timestamp(s["start"]), parse_timestamp(s["end"]))
            for s in fx.raw["spans"]
            if s["tier"] in ("B", "O")
        }
        assert excluded, "expected some break/outlier spans in the fixture"
        assert excluded.isdisjoint(set(fx.keep))
        assert set(fx.keep) == loaded - excluded

    def test_non_highlight_rallies_are_kept_as_in_play(self):
        """
        The trap this fixture exists to avoid: an N span ("failed serve",
        "average play") is not highlight-worthy but is still live ball, so the
        condense stage must keep it. Scoring N as dead time would reward a
        model for cutting real play.
        """
        fx = load_deadtime_fixture("test1")
        n_spans = [s for s in fx.raw["spans"] if s["tier"] == "N"]
        assert n_spans, "expected non-highlight rallies in the fixture"
        keep = set(fx.keep)
        for s in n_spans:
            assert (parse_timestamp(s["start"]), parse_timestamp(s["end"])) in keep

    def test_highlight_subset_matches_the_cf55_fixture(self):
        """
        Both fixtures label the same video, so the M/C spans must agree exactly.
        This is the check that catches either file drifting onto different
        footage or a re-label landing in only one of them.
        """
        dead = load_deadtime_fixture("test1")
        highlight = load_fixture("test1")
        mc = {
            (parse_timestamp(s["start"]), parse_timestamp(s["end"]))
            for s in dead.raw["spans"]
            if s["tier"] in ("M", "C")
        }
        assert mc == set(highlight.clips)
        assert dead.raw["source_video_md5"] == highlight.raw["source_video_md5"]
        assert dead.duration == highlight.video_duration_sec
