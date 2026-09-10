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
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import harness
from ml.eval.harness import FIXTURES_DIR, load_deadtime_fixture, load_fixture, parse_timestamp

# Every real dead-time case. `demo` is the format example, not a labeling pass —
# it has no video behind it, so the pinning checks below do not apply to it.
DEADTIME_IDS = sorted(
    p.name[: -len("_deadtime.json")]
    for p in FIXTURES_DIR.glob("*_deadtime.json")
    if p.name != "demo_deadtime.json"
)

# Every highlight fixture, discovered rather than named. `test1` was the only
# one when the tier checks below were written, and hardcoding it meant a second
# highlight fixture inherited none of them: one added with a mis-cased tier and
# an untagged clip left the whole suite green while the loader silently scored
# 2 of its 3 clips. Deadtime fixtures are a different shape read by a different
# loader, so they are excluded here rather than merged in.
HIGHLIGHT_IDS = sorted(
    p.stem for p in FIXTURES_DIR.glob("*.json") if not p.stem.endswith("_deadtime")
)

# The labelling vocabulary, for fixtures that declare no `tier_legend` of their
# own. Hardcoded deliberately: the alternative tried here was to fall back to
# `ground_truth_tiers`, which rejects a clip tagged with an *excluded* tier —
# and load_fixture's own comment says such a clip "stays in the file for the
# labelling record", `test_excluded_tiers_are_dropped` constructs one and
# asserts it is dropped rather than rejected, and ml/eval/README.md documents
# the format that way. A fallback that forbids the documented shape is worse
# than no fallback. Sourced from test1.json's legend: must / can / no-clip /
# break / outlier.
KNOWN_TIERS = frozenset("MCNBO")

# Which tiers are live ball, for a DEAD-TIME fixture. This is the split
# README_deadtime.md calls "the trap", and it is not the same split the
# highlight loader uses: `N` (failed serve, shank, average rally) is not
# highlight-worthy, so test1.json's `ground_truth_tiers` omits it — but it is
# still play, and the condense stage is built to keep it.
#
# Score `N` as dead time and the metrics INVERT: a model that correctly keeps a
# boring rally reads as missing dead time, and one that aggressively cuts real
# play reads as removing more of it. The harness would reward the exact failure
# it exists to catch, and the number it printed would look entirely plausible.
LIVE_BALL_TIERS = frozenset("MCN")
DEAD_TIERS = frozenset("BO")


def tier_semantics_violations(raw: dict) -> list[str]:
    """The rule itself, so the checks below and their self-tests share one copy.

    Returns a human-readable reason per violation; empty means the fixture's
    `keep_tiers` matches the live-ball/dead split above.
    """
    spans = raw.get("spans", raw.get("keep", []))
    present = {s.get("tier") for s in spans if s.get("tier") is not None}
    if not present:
        # Untagged fixtures list in-play spans only and are read permissively.
        # Nothing to check, and demanding a tier set would reject the older
        # format the loader still documents and supports.
        return []

    declared = raw.get("keep_tiers")
    if declared is None:
        return [
            "spans carry tiers but `keep_tiers` is absent, so the loader keeps "
            f"every one of {sorted(present)} as in-play — including any break "
            "or outlier, which is dead time"
        ]

    problems = []
    for tier in sorted(present - KNOWN_TIERS):
        problems.append(
            f"unknown tier {tier!r}: add it to KNOWN_TIERS and decide which side "
            "of the live-ball split it falls on"
        )
    for tier in sorted((present & LIVE_BALL_TIERS) - set(declared)):
        problems.append(
            f"tier {tier!r} is live ball but is not in keep_tiers, so those spans "
            "score as dead time — a model that correctly keeps them is penalised"
        )
    for tier in sorted(set(declared) & DEAD_TIERS):
        problems.append(
            f"tier {tier!r} is a genuine stoppage but is in keep_tiers, so the "
            "fixture claims a break is live ball"
        )
    return problems


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
        # Same file, so the same tracking space — and both halves of the harness
        # scale CF-174's contact thresholds off this number, so a disagreement
        # would score the two modes against different thresholds on one video.
        assert dead.raw["source_frame_height"] == highlight.raw["source_frame_height"]


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

    def test_the_highlight_fixtures_are_discovered(self):
        """A glob that silently matches nothing makes every case below vacuous —
        the same guard the dead-time class carries, for the same reason."""
        assert HIGHLIGHT_IDS, "no highlight fixtures found"
        assert "test1" in HIGHLIGHT_IDS, HIGHLIGHT_IDS

    @pytest.mark.parametrize("test_id", HIGHLIGHT_IDS)
    def test_every_highlight_fixture_pins_its_tracking_space(self, test_id):
        """
        `source_frame_height` is what arms `_assert_declared_frame_height`, and
        the guard returns early when the key is absent rather than failing — so
        a fixture that omits it disables the only runtime check that the source
        is the labeled one.

        The dead-time twin of this assertion has existed since CF-174, but it is
        parametrized over DEADTIME_IDS, so nothing pushed the highlight fixtures
        to acquire the key and the highlight half of the guard was inert: a
        re-encode of test1 at a different resolution would have shifted every
        find_contacts threshold, run to completion, and recorded a
        plausible-looking row. That is the exact failure the guard exists for.
        """
        raw = load_fixture(test_id).raw
        assert raw.get("source_frame_height", 0) > 0

    @pytest.mark.parametrize("test_id", HIGHLIGHT_IDS)
    def test_every_highlight_fixture_declares_a_known_tier_for_every_clip(self, test_id):
        """Guards the premise the tests above rest on, for every highlight
        fixture rather than the one that happened to exist when this was
        written.

        The filter is permissive about a missing tier, so an unlabelled clip
        that slips in is scored rather than reported. That is the right default
        for the loader and the wrong state for ground truth.

        A *wrong* tier matters as much as a missing one, and is the likelier
        authoring mistake: a value the legend does not define is neither `None`
        nor selected, so `load_fixture` drops the clip silently and the fixture
        quietly shrinks. Asserting only `is not None` could not see that —
        changing one clip's tier from "C" to "c" left the earlier version of
        this test green, and the only thing that caught it was an unrelated
        comparison against the dead-time twin, which a fixture without a twin
        does not have.
        """
        raw = load_fixture(test_id).raw
        scored = set(raw.get("ground_truth_tiers", []))

        # `tier_legend` is NOT required. It is absent from the fixture format
        # documented in ml/eval/README.md, no code reads it, and only test1.json
        # carries it — so demanding it would make the README's own example fail
        # this suite. Where a fixture declares one it is the fuller vocabulary
        # (it names the excluded tiers too) and is the better thing to validate
        # against; where it does not, fall back to the project vocabulary rather
        # than to `ground_truth_tiers`, which would reject the documented and
        # tested case of a clip tagged with an excluded tier.
        declared = set(raw["tier_legend"]) if "tier_legend" in raw else KNOWN_TIERS

        untagged = [c for c in raw["clips"] if c.get("tier") is None]
        assert not untagged, f"clips in {test_id}.json with no tier: {untagged}"

        unknown = sorted(
            {c["tier"] for c in raw["clips"] if c.get("tier") not in declared}
        )
        assert not unknown, (
            f"clips in {test_id}.json carry tiers it never declares: {unknown}. "
            f"`load_fixture` keeps only {sorted(scored)}, so an undeclared tier "
            f"is dropped silently and the fixture scores fewer clips than it "
            f"appears to contain."
        )

        assert scored <= declared, (
            f"{test_id}.json: ground_truth_tiers names {sorted(scored - declared)}, "
            f"which is not in {'its tier_legend' if 'tier_legend' in raw else 'the project vocabulary'}. "
            f"`load_fixture` would keep clips at a tier no clip can carry."
        )

        # Cheap half of "does this fixture score what it ships": every
        # highlight fixture must score *something*. An empty or fully-excluding
        # `ground_truth_tiers` produces a fixture that loads, reports no clips,
        # and scores every model output as a false positive — silent and
        # plausible and wrong, which is the failure ml/eval/README.md warns
        # about for the dead-time loader. Costs no per-fixture bookkeeping.
        assert load_fixture(test_id).clips, (
            f"{test_id}.json scores no clips at all — ground_truth_tiers "
            f"{sorted(scored)} excludes every clip it ships"
        )

    def test_test1_still_scores_every_clip_it_ships(self):
        """The shrink the checks above cannot see.

        A clip re-tiered from `C` to `N`, or a `ground_truth_tiers` narrowed to
        `["M"]`, is legal on every axis asserted above — the tier is in the
        legend and the set is a subset of it — and quietly drops clips from
        scoring. Both were green here, caught only by the dead-time twin
        comparison, which is a coincidence of this fixture having a twin.

        Asserting membership in `ground_truth_tiers` would over-reach: the
        loader is *documented* to exclude tiers, and a fixture is entitled to
        do so deliberately. What is not intended is doing it by accident, so
        this pins the count instead. If a re-labelling pass genuinely changes
        it, update the number in the same commit and the diff records the
        decision.
        """
        raw = load_fixture("test1").raw
        scored = load_fixture("test1").clips

        assert len(raw["clips"]) == 41, "test1.json ships 41 clips"
        assert len(scored) == 41, (
            f"test1.json ships {len(raw['clips'])} clips but scores "
            f"{len(scored)}. Every clip is M or C and both are in "
            f"ground_truth_tiers, so a shortfall means a clip was re-tiered "
            f"out of scoring or ground_truth_tiers was narrowed."
        )


class TestTheFrameHeightGuardSeparatesItsTwoCauses:
    """
    `_assert_declared_frame_height` now answers two different questions, and
    conflating them sends the operator to the wrong place.

    A height that disagrees with the fixture means the source is not the labeled
    video — re-upload or re-label. A height of 0 means OpenCV could not decode
    the container at all (truncated download, missing codec), and the video is
    very likely fine. Reporting the second as the first is a re-labelling errand
    against a correct fixture.

    Newly reachable on the highlight path: before test1.json declared
    `source_frame_height`, the helper returned early and never saw a 0.
    """

    def _fixture(self, declared):
        return SimpleNamespace(raw={"source_frame_height": declared} if declared else {})

    @pytest.mark.parametrize("mode,flag", [("deadtime", "--windows-json"),
                                           ("highlight", "--clips-json")])
    def test_a_zero_height_is_reported_as_a_decode_failure(self, mode, flag):
        with pytest.raises(SystemExit) as exc:
            harness._assert_declared_frame_height(
                self._fixture(360), 0, "test1", "raw/x.mp4", mode)
        msg = str(exc.value)
        assert "decode failure" in msg and "not a fixture mismatch" in msg
        assert flag in msg, "the remediation flag must match the mode"
        assert "re-label" not in msg.lower(), (
            "a decode failure must not send the operator to re-label a good fixture"
        )

    def test_a_zero_height_fails_even_when_the_fixture_declares_nothing(self):
        """
        The declared-height check is opt-in and returns early without the key.
        A broken download is not opt-in: it must fail on any fixture, or the
        guard is only armed for files that were already the most pinned.
        """
        with pytest.raises(SystemExit, match="decode failure"):
            harness._assert_declared_frame_height(
                self._fixture(None), 0, "test1", "raw/x.mp4", "highlight")

    def test_a_real_mismatch_still_reports_a_mismatch(self):
        with pytest.raises(SystemExit) as exc:
            harness._assert_declared_frame_height(
                self._fixture(360), 1080, "test1", "raw/x.mp4", "deadtime")
        assert "decode failure" not in str(exc.value)
        assert "Re-upload the labeled file, or re-label" in str(exc.value)

    def test_a_matching_height_passes(self):
        harness._assert_declared_frame_height(
            self._fixture(360), 360, "test1", "raw/x.mp4", "deadtime")


class TestTierSemanticsAcrossDeadtimeFixtures:
    """
    The live-ball split, for every dead-time fixture rather than just test1.

    `TestTest1DeadtimeFixture` already asserts this for the one file that has
    tiers today, and README_deadtime.md claims the suite "enforces this" without
    naming a fixture. It did not: those assertions are hardcoded to test1, so a
    second tiered fixture inherited none of them. That is exactly how the
    highlight half of this file went wrong before HIGHLIGHT_IDS was discovered
    rather than named — see its comment.

    It matters now because CF-375 (#475) asks for a new 1080p fixture, and its
    own Notes warn that reusing a highlight fixture's tier set here "will
    silently produce a wrong answer". Silently is the problem: the harness still
    prints a number. This turns that prose warning into a failing test.
    """

    @pytest.mark.parametrize("test_id", DEADTIME_IDS)
    def test_keep_tiers_matches_the_live_ball_split(self, test_id):
        raw = json.loads((FIXTURES_DIR / f"{test_id}_deadtime.json").read_text(encoding="utf-8"))
        problems = tier_semantics_violations(raw)
        assert not problems, f"{test_id}_deadtime.json: " + "; ".join(problems)

    def test_at_least_one_fixture_actually_declares_tiers(self):
        """
        Every check above passes vacuously on an untagged fixture, and all but
        one of ours is untagged. If the tiered fixture ever loses its tiers this
        class goes quietly green while testing nothing.
        """
        tiered = [
            test_id for test_id in DEADTIME_IDS
            if any(
                s.get("tier") is not None
                for s in json.loads(
                    (FIXTURES_DIR / f"{test_id}_deadtime.json").read_text(encoding="utf-8")
                ).get("spans", [])
            )
        ]
        assert tiered, "no dead-time fixture carries tiers; the split is untested"


class TestTierSemanticsRule:
    """
    Self-tests for the rule, on constructed fixtures — the checked-in files
    cannot exercise the failure paths, since a fixture that tripped one would
    fail the class above instead.

    These call `tier_semantics_violations` rather than restating it. A self-test
    that re-implements the rule passes against a loosened rule, which is the
    failure mode worth guarding here.
    """

    @staticmethod
    def _fixture(keep_tiers, tiers):
        spans = [
            {"start": f"00:{i:02d}", "end": f"00:{i + 1:02d}", "tier": t}
            for i, t in enumerate(tiers)
        ]
        raw = {"spans": spans}
        if keep_tiers is not None:
            raw["keep_tiers"] = keep_tiers
        return raw

    def test_the_documented_split_is_clean(self):
        assert tier_semantics_violations(self._fixture(["M", "C", "N"], "MCNBO")) == []

    def test_a_highlight_tier_set_is_rejected(self):
        """The exact mistake CF-375's Notes warn about: copying `["M", "C"]`
        from the highlight fixture, which drops every boring rally into dead
        time."""
        problems = tier_semantics_violations(self._fixture(["M", "C"], "MCNBO"))
        assert len(problems) == 1
        assert "'N' is live ball" in problems[0]

    def test_a_break_in_keep_tiers_is_rejected(self):
        problems = tier_semantics_violations(self._fixture(["M", "C", "N", "B"], "MCNB"))
        assert any("genuine stoppage" in p for p in problems)

    def test_tiered_spans_with_no_tier_set_are_rejected(self):
        problems = tier_semantics_violations(self._fixture(None, "MCNB"))
        assert len(problems) == 1
        assert "keep_tiers` is absent" in problems[0]

    def test_an_unknown_tier_is_rejected(self):
        problems = tier_semantics_violations(self._fixture(["M", "C", "N", "X"], "MCNX"))
        assert any("unknown tier 'X'" in p for p in problems)

    def test_an_untagged_fixture_is_exempt(self):
        """test2-test5 list in-play spans only and carry no tiers. The loader
        documents and supports that shape, so the rule has to stay silent on it
        rather than demanding a retrofit."""
        raw = {"keep": [{"start": "00:01", "end": "00:02"}]}
        assert tier_semantics_violations(raw) == []

    def test_a_tier_absent_from_the_spans_is_not_demanded(self):
        """A fixture whose labeling pass happened to produce no `N` spans is
        fine; the rule is about tiers present in the file, not a required set."""
        assert tier_semantics_violations(self._fixture(["M", "C"], "MCB")) == []
