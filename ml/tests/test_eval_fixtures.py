"""
Integrity checks for the checked-in ground-truth fixtures.

Everything else in the eval suite runs on synthetic intervals, so nothing
guards the real files. A fixture is hand-authored data that silently decides
every score the harness reports — a malformed span or a drifted tier set would
show up as a plausible-looking number rather than an error.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import harness
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


class TestGroundTruthTierFilter:
    """CF-93: `load_fixture` must drop clips whose tier the fixture excludes.

    The behaviour exists and is correct. What did not exist is anything holding
    it there: `test1.json`'s clips are 15 M and 26 C against
    `ground_truth_tiers: ["M", "C"]`, so **every clip in the only highlight
    fixture passes the filter**. Deleting the filter outright leaves all 94 ml
    tests green — verified, not assumed. `test_keep_tiers_filter_is_applied`
    above covers the *dead-time* fixture's `keep_tiers` on spans, which is a
    different field read by a different loader.

    So an excluded-tier clip would count toward captured % and the play buckets
    again, and the suite would report the same numbers either way — the failure
    the card describes, arriving silently.

    Written against a fixture built here rather than by adding an excluded clip
    to `test1.json`: that file is ground truth whose contents decide every score
    the harness reports, and changing it to exercise a filter would move the
    baselines it exists to hold still.
    """

    @staticmethod
    def _fixture(tmp_path, monkeypatch, **overrides):
        data = {
            "test_id": "tiertest",
            "video_duration_sec": 600.0,
            "clips": [
                {"start": "00:10", "end": "00:20", "tier": "M"},
                {"start": "00:30", "end": "00:40", "tier": "C"},
                {"start": "01:00", "end": "01:10", "tier": "N"},
                {"start": "01:30", "end": "01:40", "tier": "B"},
                {"start": "02:00", "end": "02:10", "tier": "O"},
            ],
            **overrides,
        }
        (tmp_path / "tiertest.json").write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(harness, "FIXTURES_DIR", tmp_path)
        return load_fixture("tiertest")

    def test_excluded_tiers_are_dropped(self, tmp_path, monkeypatch):
        """The card's repro, run directly: N/B/O must not reach the scorer."""
        fx = self._fixture(tmp_path, monkeypatch, ground_truth_tiers=["M", "C"])

        assert fx.clips == [(10.0, 20.0), (30.0, 40.0)]

    def test_the_declared_tier_set_decides_it_not_a_hardcoded_one(self, tmp_path, monkeypatch):
        """A filter that happened to hardcode M/C would pass the test above.

        The fixture declares which tiers are ground truth, so a fixture that
        scores N as well must get N — otherwise the field is decorative and
        CF-93's complaint stands in a new form.
        """
        fx = self._fixture(tmp_path, monkeypatch, ground_truth_tiers=["N"])

        assert fx.clips == [(60.0, 70.0)]

    def test_an_absent_tier_set_scores_everything(self, tmp_path, monkeypatch):
        """Permissive on purpose — fixtures written before tiers existed list
        highlight clips only, so filtering them to nothing would report a
        perfect miss rate rather than an error."""
        fx = self._fixture(tmp_path, monkeypatch)

        assert len(fx.clips) == 5

    def test_a_clip_with_no_tier_is_kept(self, tmp_path, monkeypatch):
        """Same reasoning one level down: an untagged clip in a tagged fixture
        is unlabelled, not excluded. Dropping it would silently discard
        hand-authored ground truth."""
        fx = self._fixture(
            tmp_path,
            monkeypatch,
            ground_truth_tiers=["M"],
            clips=[
                {"start": "00:10", "end": "00:20", "tier": "M"},
                {"start": "00:30", "end": "00:40"},
                {"start": "01:00", "end": "01:10", "tier": "O"},
            ],
        )

        assert fx.clips == [(10.0, 20.0), (30.0, 40.0)]

    def test_the_shipped_fixture_declares_a_tier_for_every_clip(self):
        """Guards the premise the tests above rest on.

        The filter is permissive about a missing tier, so an unlabelled clip
        that slips into `test1.json` is scored rather than reported. That is the
        right default for the loader and the wrong state for ground truth.
        """
        raw = load_fixture("test1").raw
        untagged = [c for c in raw["clips"] if c.get("tier") is None]

        assert not untagged, f"clips in test1.json with no tier: {untagged}"
        assert set(raw["ground_truth_tiers"]) <= set(raw["tier_legend"]), (
            "ground_truth_tiers names a tier the legend does not define"
        )
