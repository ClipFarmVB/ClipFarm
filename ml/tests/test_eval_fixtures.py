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

from ml.eval.harness import FIXTURES_DIR, load_deadtime_fixture, load_fixture, parse_timestamp

# Every real dead-time case. `demo` is the format example, not a labeling pass —
# it has no video behind it, so the pinning checks below do not apply to it.
DEADTIME_IDS = sorted(
    p.name[: -len("_deadtime.json")]
    for p in FIXTURES_DIR.glob("*_deadtime.json")
    if p.name != "demo_deadtime.json"
)


class TestEveryDeadtimeFixture:
    """
    Well-formedness for all of them, so a fixture added later inherits the
    checks instead of relying on someone remembering to write them. The class
    below stays test1-specific: those assertions are about that file's tier
    semantics and its pairing with the CF-55 highlight fixture.
    """

    def test_the_fixtures_are_discovered(self):
        """A glob that silently matches nothing would make every test below vacuous."""
        assert len(DEADTIME_IDS) >= 5, DEADTIME_IDS

    @pytest.mark.parametrize("test_id", DEADTIME_IDS)
    def test_spans_are_ordered_and_disjoint(self, test_id):
        fx = load_deadtime_fixture(test_id)
        assert fx.duration > 0, "fixture needs video_duration_sec"
        assert fx.keep, "fixture yielded no in-play spans"
        prev_end = 0.0
        for start, end in fx.keep:
            assert start < end, f"{test_id}: non-positive span at {start}"
            assert start >= prev_end, f"{test_id}: span at {start} overlaps the previous one"
            prev_end = end

    @pytest.mark.parametrize("test_id", DEADTIME_IDS)
    def test_video_is_pinned_by_content_hash_and_tracking_space(self, test_id):
        """
        md5 keys the ball cache; source_frame_height is the tracking space the
        guarded path normalises speeds into — it divides by the height to get
        frame-heights/s, so one set of thresholds covers 360p and 1080p games.
        deadtime_variants.load_game defaults a missing height to 1080, so a 360p
        fixture that omits it has every speed divided by 3 and silently scores
        against thresholds 3x too strict, while looking plausible doing it.
        """
        raw = load_deadtime_fixture(test_id).raw
        assert len(raw.get("source_video_md5", "")) == 32, "expected a 32-char content MD5"
        assert raw.get("source_frame_height", 0) > 0

    def test_labels_fit_inside_the_declared_duration(self):
        """
        `video_duration_sec` anchors the dead-time complement, and the harness
        clamps anything past it *silently* — so an over-running label is lost
        rather than flagged, and the seconds it covered turn into dead time the
        model is then rewarded for cutting.

        Asserted as a set rather than per fixture so this stays honest in both
        directions: a new fixture that over-runs fails, and fixing a known one
        also fails, which is the reminder to delete it from the list.
        """
        known_bad = {
            # Its fixture note records this and asks for a check against the
            # labeling copy: final span 20:24-20:30 vs a 20:23 video. Unresolved
            # — needs the labeler, not a guess about which end is wrong.
            "test4",
        }
        over_running = set()
        for test_id in DEADTIME_IDS:
            data = json.loads(
                (FIXTURES_DIR / f"{test_id}_deadtime.json").read_text(encoding="utf-8"))
            spans = data.get("spans", data.get("keep", []))
            # Guarded rather than max()'d directly: an empty span list is a
            # broken fixture, and a bare max() reports it as a ValueError from
            # inside a test about over-running timestamps.
            assert spans, f"{test_id}: fixture has no spans"
            if max(parse_timestamp(s["end"]) for s in spans) > data["video_duration_sec"]:
                over_running.add(test_id)
        assert over_running == known_bad


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
