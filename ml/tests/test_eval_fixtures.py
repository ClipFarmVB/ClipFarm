"""
Integrity checks for the checked-in ground-truth fixtures.

Everything else in the eval suite runs on synthetic intervals, so nothing
guards the real files. A fixture is hand-authored data that silently decides
every score the harness reports — a malformed span or a drifted tier set would
show up as a plausible-looking number rather than an error.
"""
import json
import math
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

# Dead-time fixtures with a highlight fixture for the same video, which the
# copied-clip-list check compares against. One list feeds both that check and
# its vacuity guard, so an empty list fails the guard rather than silently
# collecting nothing.
DEADTIME_WITH_HIGHLIGHT = [t for t in DEADTIME_IDS if t in HIGHLIGHT_IDS]

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


def _spans(raw: dict) -> list[dict]:
    """The fixture's span list. `keep` is the legacy name the loader still reads."""
    return raw.get("spans", raw.get("keep", []))


def highlight_wellformedness_violations(raw: dict, scored: list[tuple[float, float]]) -> list[str]:
    """Well-formedness for a HIGHLIGHT fixture. Empty means nothing to report.

    Two lists, deliberately, because the two rules have different strengths.

    `end > start` and "inside `video_duration_sec`" are checked over **every
    labelled clip**, scored or not. A reversed or over-running span is a typo
    whatever tier carries it, and it stays in the file as part of the labelling
    record — the card's failing case is `0:32` typed where `0:23` was meant, and
    that costs nothing to detect and never has a legitimate form.

    Ordering and disjointness are checked over the **scored** clips only, and
    this half is a judgement call rather than a documented rule — said plainly,
    because the reasoning is the argument for it. What the repo does establish
    is that the loader keeps an excluded-tier clip in the file while dropping it
    from scoring (`test_excluded_tiers_are_dropped`, and `load_fixture`'s own
    comment, "stays in the file for the labelling record"). Nothing in
    `ml/eval/README.md` says whether such a clip may *overlap* a scored one, and
    `test1.json` carries no `B` or `O` clip to settle it either way. Allowing it
    is the reading that cannot block valid labelling: a camera-outlier span
    covering a stretch that contains a rally is an ordinary thing to annotate,
    and a guard that rejected it would be discovered by whoever writes the
    second highlight fixture. Over the scored list there is no such doubt —
    those spans are what the metric sums, and an overlap there double-counts.

    The split is not visible in today's only fixture, where all 41 clips score
    and both lists are identical, so it is pinned by constructed cases in
    `TestHighlightWellFormedness` rather than by the fixture.
    """
    if not isinstance(raw, dict):
        return [f"the fixture is {type(raw).__name__}, not an object"]

    problems = []

    duration = raw.get("video_duration_sec")
    # `bool` before `(int, float)`, because `isinstance(True, int)` is True and
    # `True` would survive as the number 1, reporting every clip as over-running
    # "the declared Trues video". And `math.isfinite`, because `json.loads`
    # accepts a bare `NaN` and `isinstance(nan, float)` is True — every
    # comparison against NaN is False, so a NaN duration would exempt every clip
    # from the over-run rule and report nothing at all. That is word for word
    # the silent disarming the absent-key branch below exists to make loud.
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
    ):
        problems.append(
            f"`video_duration_sec` is {duration!r}, which no clip can be "
            f"compared against")
        duration = None
    elif duration is None:
        # Not a skip. `load_fixture` types this `float | None` and the
        # highlight format does not require it, which is exactly why an absent
        # key has to be loud: it would silently disarm the over-run check while
        # every other assertion still passed, and the dead-time twin indexes
        # the key directly rather than tolerating its absence.
        problems.append(
            "no `video_duration_sec`, so nothing anchors the over-run check")

    clips = raw.get("clips", [])
    if not isinstance(clips, list):
        # Otherwise a dict iterates as its keys and a string as its characters,
        # and every one of them reports "has no start or end" — a true message
        # about the wrong thing.
        return problems + [f"`clips` is {type(clips).__name__}, not a list"]
    if not clips:
        # The dead-time twin asserts `fx.keep` for the same reason: a fixture
        # with no clips passes every check below vacuously, and a check that
        # cannot fail on an empty fixture is the failure the parametrized
        # version exists to prevent.
        problems.append("no clips at all")

    for clip in clips:
        if not isinstance(clip, dict):
            # A bare string in the list would otherwise substring-test for
            # "start" and then raise on the subscript.
            problems.append(f"clip {clip!r} is {type(clip).__name__}, not an object")
            continue
        missing = [k for k in ("start", "end") if k not in clip]
        if missing:
            # Reported rather than raised: a bare KeyError from inside a
            # well-formedness check reads like a broken test, not a broken
            # fixture, and it stops the remaining clips being looked at.
            problems.append(f"clip {clip!r} has no {' or '.join(missing)}")
            continue
        if any(isinstance(clip[k], bool) for k in ("start", "end")):
            # `parse_timestamp(True)` is 1.0 and scores silently.
            problems.append(f"clip {clip['start']!r}-{clip['end']!r} is a boolean")
            continue
        try:
            start = parse_timestamp(clip["start"])
            end = parse_timestamp(clip["end"])
        except (ValueError, TypeError) as exc:
            # A PRESENT but unparseable timestamp has the same consequences as
            # an absent one, and is the likelier typo: `0O:23` with a letter O,
            # a `null` left by a labelling tool, `1:2:3:4`. Fixing the missing
            # key and leaving this raising would be closing the spelling and
            # not the class.
            problems.append(
                f"clip {clip['start']!r}-{clip['end']!r} has an unreadable "
                f"timestamp ({exc})")
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            problems.append(
                f"clip {clip['start']!r}-{clip['end']!r} is not a finite time")
            continue
        if start < 0:
            problems.append(f"clip {clip['start']}-{clip['end']} starts before zero")
        if end <= start:
            problems.append(
                f"clip {clip['start']}-{clip['end']} is not a positive span")
        if duration is not None and end > duration:
            problems.append(
                f"clip {clip['start']}-{clip['end']} ends past the declared "
                f"{duration}s video")

    if clips and not scored:
        # The twin of "no clips at all", one list over — and the list that
        # matters more, since `evaluate()` sums the SCORED spans. A fixture
        # whose `ground_truth_tiers` excludes every clip it ships loads fine,
        # scores nothing, and makes every ordering check below vacuous while
        # this function returns clean. `test_test1_still_scores_every_clip_it
        # _ships` hides it today by pinning 41 for test1 alone; `HIGHLIGHT_IDS`
        # is a glob precisely because a second fixture is expected, and that one
        # would inherit the ordering checks in name and not in substance.
        problems.append(
            f"{len(clips)} clips and none of them score, so nothing below "
            f"checks anything")

    # One message for both shapes, because the loader preserves file order and
    # `start < prev_end` is the same defect either way: a clip listed out of
    # order and a clip genuinely overlapping its predecessor both mean the
    # scored spans are not a partition of the labelled play.
    prev_end = None
    for start, end in scored:
        if prev_end is not None and start < prev_end:
            problems.append(
                f"scored clip at {start} starts before the previous one ended "
                f"({prev_end}) — out of order, or overlapping")
        prev_end = end if prev_end is None else max(prev_end, end)

    return problems


def tier_semantics_violations(raw: dict) -> list[str]:
    """The rule itself, so the checks below and their self-tests share one copy.

    Returns a human-readable reason per violation; empty means the fixture's
    `keep_tiers` matches the live-ball/dead split above.
    """
    spans = _spans(raw)
    present = {s.get("tier") for s in spans if s.get("tier") is not None}
    if not present:
        # Untagged fixtures list in-play spans only and are read permissively.
        # Nothing to check, and demanding a tier set would reject the older
        # format the loader still documents and supports.
        return []

    declared = raw.get("keep_tiers")
    if declared is None:
        stoppages = sorted(present & DEAD_TIERS)
        consequence = (
            f" — including the {stoppages} spans, which are dead time"
            if stoppages
            else "; declare it, so the live-ball split is stated rather than implied"
        )
        return [
            "spans carry tiers but `keep_tiers` is absent, so the loader keeps "
            f"every one of {sorted(present)} as in-play{consequence}"
        ]

    problems = []

    # An untagged span in a TIERED fixture is the hole this rule had first.
    # `load_deadtime_fixture` keeps a span with no tier unconditionally,
    # whatever `keep_tiers` says — so a labeller who tags the rallies they cared
    # about and leaves the breaks bare produces a fixture that passes every
    # check below while scoring those breaks as live ball. Same harm as the
    # trap, other direction: dead time under-counted, and a model that
    # correctly cuts the break is scored as over-cutting real play.
    #
    # Partial tagging is never deliberate in this format — test1_deadtime.json
    # tags all 132 — and it is the likely slip for a labeller moving from the
    # untagged shape of test2-test5, which is exactly who CF-375 sends here.
    untagged = sum(1 for s in spans if s.get("tier") is None)
    if untagged:
        problems.append(
            f"{untagged} of {len(spans)} spans carry no tier while the fixture is "
            "tiered; the loader keeps an untagged span as in-play no matter what "
            "keep_tiers says, so a bare break would score as live ball. Tag every "
            "span"
        )

    # Checked over the DECLARED set too, not just the tiers in use: a typo'd or
    # mis-cased entry in keep_tiers that happens to match no span changes no
    # score today, but it silently means something other than it reads.
    for tier in sorted((present | set(declared)) - KNOWN_TIERS):
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


def copied_clip_list(deadtime_raw: dict, highlight_raw: dict) -> bool:
    """Whether a dead-time fixture's live-ball spans are its highlight sibling's clips.

    `tier_semantics_violations` compares `keep_tiers` with the tiers present in
    the file, so it cannot see the trap in its primary form: a highlight clip
    list (tiers `M`/`C` only) pasted in as the spans, with `["M", "C"]` as
    `keep_tiers`. Nothing in that file is inconsistent — the boring rallies it
    should have labelled were never written — so the tier rule passes it. What
    gives it away is the pairing: every live-ball span is one of the sibling's
    clips, and a dead-time pass over the same video labels the rallies the
    highlight pass left out.

    Stoppage spans (`B`, `O`) are left out of the comparison. They are never
    highlight clips, so counting them would let a copied list escape as soon as
    its breaks are tagged too — the natural way to make this mistake.
    """
    def _times(items) -> set[tuple[float, float]]:
        return {(parse_timestamp(s["start"]), parse_timestamp(s["end"])) for s in items}

    live = _times(s for s in _spans(deadtime_raw) if s.get("tier") not in DEAD_TIERS)
    clips = _times(highlight_raw.get("clips", []))
    return bool(live) and live <= clips


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

    @pytest.mark.parametrize("test_id", HIGHLIGHT_IDS)
    def test_every_highlight_fixture_is_well_formed(self, test_id):
        """CF-309: `load_fixture` validates nothing, and the harness is quiet
        about it.

        `metrics.union()` drops a span with `end <= start` and `evaluate()`
        still counts it in the human total, so a reversed clip scores as a
        clip the model can never hit — permanently, with plausible-looking
        recall numbers and nothing to catch it. A span past
        `video_duration_sec` is clamped silently for the same shape of harm.
        `ml/eval/README.md` advertises that adding a case "needs no code
        change", which makes a hand-typed `0:32` for `0:23` the likely way in.

        The dead-time fixtures have carried these checks since CF-174
        (`TestEveryDeadtimeFixture`); the highlight loader is a different
        function reading a different shape, and it never acquired them.
        """
        fx = load_fixture(test_id)
        problems = highlight_wellformedness_violations(fx.raw, fx.clips)
        assert not problems, f"{test_id}.json: " + "; ".join(problems)

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


class TestHighlightWellFormedness:
    """Self-tests for the rule above, because the fixture cannot exercise it.

    `test1.json` is the only highlight fixture, all 41 of its clips score, and
    it is clean on every axis — so the parametrized check passes without ever
    distinguishing the raw list from the scored one, and a revert that swapped
    them would stay green. These construct the cases the fixture does not
    supply, in both directions: what must fail, and what must be allowed to
    pass.
    """

    @staticmethod
    def _raw(clips, duration=600.0):
        return {"test_id": "t", "video_duration_sec": duration, "clips": clips}

    @staticmethod
    def _scored(raw, tiers=None):
        """The spans `load_fixture` would score, without touching the disk."""
        return [
            (parse_timestamp(c["start"]), parse_timestamp(c["end"]))
            for c in raw["clips"]
            if tiers is None or c.get("tier") is None or c["tier"] in tiers
        ]

    def test_a_clean_fixture_reports_nothing(self):
        raw = self._raw([{"start": "00:10", "end": "00:20"},
                         {"start": "00:30", "end": "00:40"}])
        assert highlight_wellformedness_violations(raw, self._scored(raw)) == []

    def test_a_reversed_span_is_caught(self):
        """The card's failing case: `0:32` typed where `0:23` was meant."""
        raw = self._raw([{"start": "00:32", "end": "00:23"}])
        problems = highlight_wellformedness_violations(raw, self._scored(raw))
        assert any("not a positive span" in p for p in problems), problems

    def test_a_zero_length_span_is_caught(self):
        raw = self._raw([{"start": "00:20", "end": "00:20"}])
        assert highlight_wellformedness_violations(raw, self._scored(raw))

    def test_a_span_past_the_declared_duration_is_caught(self):
        raw = self._raw([{"start": "09:50", "end": "10:30"}], duration=600.0)
        problems = highlight_wellformedness_violations(raw, self._scored(raw))
        assert any("ends past the declared" in p for p in problems), problems

    def test_a_span_ending_exactly_at_the_duration_is_fine(self):
        """The boundary belongs inside. A clip running to the final frame is
        ordinary, and `metrics` clamps at the duration rather than past it."""
        raw = self._raw([{"start": "09:50", "end": "10:00"}], duration=600.0)
        assert highlight_wellformedness_violations(raw, self._scored(raw)) == []

    def test_a_missing_duration_is_reported_rather_than_skipped(self):
        """It is optional in the format, which is why its absence has to be
        loud: the over-run check silently stops existing otherwise."""
        raw = {"test_id": "t", "clips": [{"start": "00:10", "end": "00:20"}]}
        problems = highlight_wellformedness_violations(raw, self._scored(raw))
        assert any("video_duration_sec" in p for p in problems), problems

    def test_overlapping_scored_clips_are_caught(self):
        raw = self._raw([{"start": "00:10", "end": "00:30"},
                         {"start": "00:20", "end": "00:40"}])
        problems = highlight_wellformedness_violations(raw, self._scored(raw))
        assert any("out of order, or overlapping" in p for p in problems), problems

    def test_clips_listed_out_of_order_are_caught(self):
        raw = self._raw([{"start": "00:30", "end": "00:40"},
                         {"start": "00:10", "end": "00:20"}])
        assert highlight_wellformedness_violations(raw, self._scored(raw))

    def test_an_excluded_tier_clip_may_overlap_a_scored_one(self):
        """The case that decides raw-vs-scored, and the reason the two rules
        read different lists.

        An `O` (outlier) annotation covering a stretch that contains a scored
        rally is treated as valid labelling. That is a judgement, not a cited
        rule: `test_excluded_tiers_are_dropped` pins only that such a clip is
        kept in the file and out of scoring, and no README says whether it may
        overlap. Checking disjointness over the raw list would forbid the
        shape, so it is checked over the scored spans, where an overlap really
        does double-count.
        """
        raw = self._raw([{"start": "00:10", "end": "00:20", "tier": "M"},
                         {"start": "00:15", "end": "00:50", "tier": "O"},
                         {"start": "00:30", "end": "00:40", "tier": "M"}])
        assert highlight_wellformedness_violations(raw, self._scored(raw, {"M"})) == []

    def test_but_an_excluded_tier_clip_is_still_checked_for_a_typo(self):
        """The other half of that split. Dropping it from scoring does not make
        a reversed timestamp acceptable — it stays in the labelling record, and
        the record is what a later pass reads."""
        raw = self._raw([{"start": "00:10", "end": "00:20", "tier": "M"},
                         {"start": "00:50", "end": "00:15", "tier": "O"}])
        problems = highlight_wellformedness_violations(raw, self._scored(raw, {"M"}))
        assert any("not a positive span" in p for p in problems), problems

    def test_a_fixture_with_no_clips_is_reported(self):
        """Otherwise every rule above passes vacuously on it, which is what the
        parametrized check exists to stop."""
        raw = self._raw([])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("no clips at all" in p for p in problems), problems

    def test_a_clip_missing_a_timestamp_is_reported_not_raised(self):
        """A bare `KeyError` here reads like a broken test rather than a broken
        fixture, and it stops the remaining clips being looked at."""
        raw = self._raw([{"start": "00:10"}, {"start": "00:50", "end": "00:20"}])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("has no end" in p for p in problems), problems
        # The clip after the malformed one is still checked.
        assert any("not a positive span" in p for p in problems), problems

    def test_a_fixture_that_scores_none_of_its_clips_is_reported(self):
        """The twin of the empty-clips rule, on the list the metric sums.

        A fixture whose `ground_truth_tiers` excludes everything it ships loads
        fine and scores nothing; every ordering assertion is then vacuous and
        the helper returns clean. Caught here rather than by
        `test_test1_still_scores_every_clip_it_ships`, which pins 41 for `test1`
        and says nothing about the second fixture the glob exists to admit.
        """
        raw = self._raw([{"start": "00:10", "end": "00:20", "tier": "O"},
                         {"start": "00:30", "end": "00:40", "tier": "B"}])
        problems = highlight_wellformedness_violations(raw, self._scored(raw, {"M"}))
        assert any("none of them score" in p for p in problems), problems

    def test_a_negative_timestamp_is_reported(self):
        raw = self._raw([{"start": "-5", "end": "00:20"}])
        problems = highlight_wellformedness_violations(raw, self._scored(raw))
        assert any("starts before zero" in p for p in problems), problems

    def test_an_unreadable_timestamp_is_reported_not_raised(self):
        """The neighbour of the missing key, and the likelier typo: `0O:23`
        with a letter O is exactly the hand-authoring slip the card describes,
        and `parse_timestamp` raises on it from inside the check."""
        raw = self._raw([{"start": "0O:23", "end": "00:40"},
                         {"start": "00:50", "end": "00:20"}])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("unreadable timestamp" in p for p in problems), problems
        # The clip after it is still checked.
        assert any("not a positive span" in p for p in problems), problems

    @pytest.mark.parametrize("bad", [None, ["00:10"], "1:2:3:4", "0O:23", ""])
    def test_every_unreadable_timestamp_shape_is_reported(self, bad):
        """The class, not the spelling: a `null` left by a labelling tool, a
        list, one colon too many, a letter O for a zero, an empty string.

        A bare NUMBER is deliberately absent from this list — `parse_timestamp`
        reads `12` as twelve seconds, which is the documented single-part form,
        so it is well-formed rather than unreadable. Found by putting it here
        and watching the test fail.
        """
        raw = self._raw([{"start": bad, "end": "00:40"}])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("unreadable timestamp" in p for p in problems), problems

    def test_a_clips_key_that_is_not_a_list_is_reported_as_that(self):
        """A dict iterates as its keys and a string as its characters, so
        without this every entry reports "has no start or end" — true messages
        about entirely the wrong thing."""
        raw = self._raw([])
        raw["clips"] = {"start": "00:10", "end": "00:20"}
        problems = highlight_wellformedness_violations(raw, [])
        assert any("not a list" in p for p in problems), problems

    def test_a_clip_that_is_not_an_object_is_reported(self):
        raw = self._raw(["00:10-00:20"])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("not an object" in p for p in problems), problems

    def test_a_non_numeric_duration_is_reported_and_disarms_only_itself(self):
        """Comparing a float to a string raises; reporting it keeps the
        positive-span rule running over the same fixture, which is asserted
        below rather than only claimed."""
        raw = self._raw([{"start": "00:50", "end": "00:20"}], duration="10:00")
        problems = highlight_wellformedness_violations(raw, [])
        assert any("no clip can be compared against" in p for p in problems), problems
        assert any("not a positive span" in p for p in problems), problems

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, False, "10:00"])
    def test_a_duration_no_clip_can_be_compared_against_is_reported(self, bad):
        """NaN is the one that matters. `json.loads` accepts a bare `NaN`,
        `isinstance(nan, float)` is True, and every comparison against it is
        False — so a NaN duration would exempt every clip from the over-run rule
        and report nothing, which is exactly the silent disarming the
        absent-key branch exists to make loud. `True` is the same shape:
        `isinstance(True, int)` is True, so it survives as the number 1."""
        raw = self._raw([{"start": "00:10", "end": "00:20"}], duration=bad)
        problems = highlight_wellformedness_violations(raw, [])
        assert any("no clip can be compared against" in p for p in problems), problems

    def test_a_nan_timestamp_is_reported(self):
        """`nan < 0` is False, `end <= nan` is False, `end > duration` is False
        — a NaN span passes every rule by making each comparison False."""
        raw = self._raw([{"start": float("nan"), "end": "00:20"}])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("not a finite time" in p for p in problems), problems

    def test_a_boolean_timestamp_is_reported(self):
        """`parse_timestamp(True)` is 1.0 and would score silently."""
        raw = self._raw([{"start": True, "end": "00:20"}])
        problems = highlight_wellformedness_violations(raw, [])
        assert any("is a boolean" in p for p in problems), problems

    def test_a_fixture_that_is_not_an_object_is_reported(self):
        """The shape the four checks above removed, one level up."""
        problems = highlight_wellformedness_violations(["00:10"], [])
        assert any("not an object" in p for p in problems), problems

    def test_the_real_fixture_exercises_the_scored_path_at_all(self):
        """A control. If `test1` ever stopped scoring anything, the
        parametrized check above would pass vacuously on the ordering half."""
        assert len(load_fixture("test1").clips) > 1


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

    It matters now because CF-375 (#475) sends someone to label a new fixture.
    #475's own Notes warn about the other direction — a dead-time fixture reused
    as the highlight fixture — but the tier trap is the same either way, and a
    new dead-time fixture is where this class catches it. The failure is silent
    (the harness still prints a number), so README_deadtime.md's prose warning is
    turned into a failing test here.
    """

    # `demo` included: this rule reads only the JSON, so the reason `demo` is
    # excluded from the pinning checks (no video behind it) does not apply.
    @pytest.mark.parametrize("test_id", DEADTIME_IDS + ["demo"])
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
                for s in _spans(
                    json.loads(
                        (FIXTURES_DIR / f"{test_id}_deadtime.json").read_text(encoding="utf-8")
                    )
                )
            )
        ]
        assert tiered, "no dead-time fixture carries tiers; the split is untested"

    @pytest.mark.parametrize("test_id", DEADTIME_WITH_HIGHLIGHT)
    def test_the_spans_are_not_a_copy_of_the_highlight_clip_list(self, test_id):
        dead = json.loads((FIXTURES_DIR / f"{test_id}_deadtime.json").read_text(encoding="utf-8"))
        high = json.loads((FIXTURES_DIR / f"{test_id}.json").read_text(encoding="utf-8"))
        assert not copied_clip_list(dead, high), (
            f"every live-ball span in {test_id}_deadtime.json is a clip from "
            f"{test_id}.json: a highlight clip list reused as the in-play set, which "
            "scores every boring rally as dead time (README_deadtime.md, 'The trap')"
        )

    def test_at_least_one_dead_time_fixture_has_a_highlight_sibling(self):
        """The copy check above runs once per pair, so with no pair it tests nothing."""
        assert DEADTIME_WITH_HIGHLIGHT, (
            "no dead-time fixture has a highlight sibling; the copied-clip-list check is untested"
        )


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
        """The tier trap in the direction this class guards: a dead-time
        fixture given the highlight fixture's `["M", "C"]`, which drops every
        boring rally into dead time. (#475's Notes warn about the reverse copy;
        see the class docstring above.)"""
        problems = tier_semantics_violations(self._fixture(["M", "C"], "MCNBO"))
        assert any("'N' is live ball" in p for p in problems), problems

    def test_a_break_in_keep_tiers_is_rejected(self):
        problems = tier_semantics_violations(self._fixture(["M", "C", "N", "B"], "MCNB"))
        assert any("genuine stoppage" in p for p in problems)

    def test_tiered_spans_with_no_tier_set_are_rejected(self):
        problems = tier_semantics_violations(self._fixture(None, "MCNB"))
        assert any("keep_tiers` is absent" in p for p in problems), problems

    def test_an_unknown_tier_is_rejected(self):
        problems = tier_semantics_violations(self._fixture(["M", "C", "N", "X"], "MCNX"))
        assert any("unknown tier 'X'" in p for p in problems)

    def test_an_untagged_fixture_is_exempt(self):
        """test2-test5 list in-play spans only and carry no tiers. The loader
        documents and supports that shape, so the rule has to stay silent on it
        rather than demanding a retrofit."""
        raw = {"keep": [{"start": "00:01", "end": "00:02"}]}
        assert tier_semantics_violations(raw) == []

    def test_a_bare_span_in_a_tiered_fixture_is_rejected(self):
        """The hole the cold round found: `load_deadtime_fixture` keeps an
        untagged span as in-play regardless of `keep_tiers`, so a labeller who
        tags the rallies and leaves the breaks bare gets a fixture that reads
        clean and scores a BREAK as live ball."""
        raw = self._fixture(["M", "C", "N"], "MCN")
        raw["spans"].append({"start": "00:30", "end": "00:40", "note": "BREAK"})
        problems = tier_semantics_violations(raw)
        assert any("carry no tier" in p for p in problems), problems

    def test_an_unknown_tier_declared_but_unused_is_rejected(self):
        """A typo in `keep_tiers` that matches no span changes no score today,
        but it does not mean what it reads."""
        raw = self._fixture(["M", "C", "N", "Z"], "MCN")
        problems = tier_semantics_violations(raw)
        assert any("unknown tier 'Z'" in p for p in problems), problems

    def test_the_legacy_keep_key_is_read_too(self):
        """`load_deadtime_fixture` accepts `keep` as the original name for
        `spans`. The rule has to read the same list the loader does, or a
        tiered fixture written the old way is scored but never checked."""
        raw = {"keep": [{"start": "00:00", "end": "00:10", "tier": "N"}],
               "keep_tiers": ["M", "C"]}
        problems = tier_semantics_violations(raw)
        assert any("'N' is live ball" in p for p in problems), problems

    def test_a_fully_tagged_fixture_with_no_stoppages_is_still_clean(self):
        """The untagged-span rule must not fire when every span is tagged, even
        with no break or outlier in the file — a shape the documented-split test
        above, whose spans include `B` and `O`, does not exercise."""
        assert tier_semantics_violations(self._fixture(["M", "C", "N"], "MCN")) == []

    def test_a_tier_absent_from_the_spans_is_not_demanded(self):
        """A fixture whose labeling pass happened to produce no `N` spans is
        fine; the rule is about tiers present in the file, not a required set.
        The copied-clip-list form of that shape is caught separately, against the
        fixture's highlight sibling."""
        assert tier_semantics_violations(self._fixture(["M", "C"], "MCB")) == []

    def test_every_known_tier_is_on_exactly_one_side_of_the_split(self):
        """A new letter must be placed as live ball or as dead time, not both or neither.

        The unknown-tier message tells the author to add a tier to KNOWN_TIERS
        "and decide which side of the live-ball split it falls on". Without this
        only the first half was enforced: a letter added to KNOWN_TIERS alone was
        accepted whether or not a fixture kept it.
        """
        assert LIVE_BALL_TIERS.isdisjoint(DEAD_TIERS), sorted(LIVE_BALL_TIERS & DEAD_TIERS)
        assert KNOWN_TIERS == LIVE_BALL_TIERS | DEAD_TIERS, (
            f"KNOWN_TIERS {sorted(KNOWN_TIERS)} must equal LIVE_BALL_TIERS | DEAD_TIERS "
            f"{sorted(LIVE_BALL_TIERS | DEAD_TIERS)}"
        )

    def test_a_copied_clip_list_is_detected(self):
        clips = [{"start": "00:10", "end": "00:20", "tier": "M"},
                 {"start": "00:30", "end": "00:40", "tier": "C"}]
        assert copied_clip_list({"spans": clips, "keep_tiers": ["M", "C"]}, {"clips": clips})

    def test_a_copied_subset_of_the_clip_list_is_detected(self):
        clips = [{"start": "00:10", "end": "00:20", "tier": "M"},
                 {"start": "00:30", "end": "00:40", "tier": "C"}]
        assert copied_clip_list({"spans": clips[:1]}, {"clips": clips})

    def test_a_copied_clip_list_with_its_stoppages_tagged_is_detected(self):
        """The natural way to make the mistake: copy the clips, then tag the breaks.

        A `B`/`O` span is never a highlight clip, so a comparison over every span
        would stop matching the moment one is added.
        """
        clips = [{"start": "00:10", "end": "00:20", "tier": "M"},
                 {"start": "00:30", "end": "00:40", "tier": "C"}]
        spans = clips + [{"start": "05:00", "end": "08:00", "tier": "B"},
                         {"start": "09:00", "end": "09:30", "tier": "O"}]
        assert copied_clip_list({"spans": spans, "keep_tiers": ["M", "C"]}, {"clips": clips})

    def test_a_real_pass_with_rallies_the_highlights_omit_is_not_a_copy(self):
        clips = [{"start": "00:10", "end": "00:20", "tier": "M"}]
        spans = clips + [{"start": "01:00", "end": "01:05", "tier": "N"}]
        assert not copied_clip_list({"spans": spans, "keep_tiers": ["M", "C", "N"]}, {"clips": clips})

    def test_an_empty_span_list_is_not_a_copy(self):
        assert not copied_clip_list({"spans": []}, {"clips": [{"start": "00:10", "end": "00:20"}]})
