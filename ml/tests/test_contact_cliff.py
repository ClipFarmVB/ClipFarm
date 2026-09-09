"""
CF-376 (#476): the segmentation split out of contacts_to_rallies, and the
attrition ladder built on it.

Two things are under test and they are not the same thing.

`contact_segments` is a *refactor* — steps 1 and 2 of `contacts_to_rallies`,
lifted out so ml/eval can ask what the segments looked like before either
noise gate ran. The tests for it are equivalence tests: the rallies
`contacts_to_rallies` emits must not move, and the segments must include the
1- and 2-contact ones the gate then deletes, because those are the population
the card is about.

`Ladder` is the accounting. It is tested against hand-built contact lists
rather than a ball track: a track that yields a segment of exactly two
contacts on demand is much harder to construct than the two contacts, and the
gates are what is under test, not find_contacts.
"""
import pytest

from ml.eval.contact_cliff import Ladder, report
from ml.pipeline import ball as B


def contacts_at(*times: float) -> list[dict]:
    """Minimal contacts. Only `time` reaches the grouping or either gate."""
    return [{"time": t, "action": "unknown", "action_confidence": 0.0} for t in times]


# ── contact_segments: the extraction ─────────────────────────────────────────

def test_a_gap_over_the_rally_threshold_starts_a_new_segment():
    gap = B.RALLY_GAP_SECONDS
    segments = B.contact_segments(contacts_at(0.0, 1.0, 1.0 + gap + 0.5, 1.0 + gap + 1.5))
    assert [len(s) for s in segments] == [2, 2]


def test_a_gap_exactly_at_the_threshold_does_not_split():
    # The production condition is `>`, not `>=`. A test that used a gap safely
    # past the boundary would pass against either.
    segments = B.contact_segments(contacts_at(0.0, B.RALLY_GAP_SECONDS))
    assert [len(s) for s in segments] == [2]


def test_short_segments_survive_segmentation_because_the_gate_is_downstream():
    # The whole reason this function is separate: `contacts_to_rallies` never
    # exposes these, and they are exactly what CF-376 needs counted.
    gap = B.RALLY_GAP_SECONDS + 1.0
    segments = B.contact_segments(contacts_at(0.0, gap, 2 * gap, 2 * gap + 0.5))
    assert sorted(len(s) for s in segments) == [1, 1, 2]


def test_a_group_longer_than_the_clip_cap_is_subdivided():
    # Contacts spanning well over MAX_CLIP_DURATION with no gap large enough to
    # split them, so only step 2 can break this up.
    step = B.RALLY_GAP_SECONDS - 0.5
    times = [i * step for i in range(int(B.MAX_CLIP_DURATION / step) + 4)]
    segments = B.contact_segments(contacts_at(*times))
    assert len(segments) > 1
    # Every sub-group under the cap, asserted directly rather than as a
    # disjunction with `len(seg) < 4`. The disjunctive form is satisfied by
    # several wrong implementations — see the unreachability test below for why
    # the second arm can never rescue one here.
    for seg in segments:
        assert seg[-1]["time"] - seg[0]["time"] <= B.MAX_CLIP_DURATION


def test_a_group_spanning_exactly_the_cap_is_left_alone():
    """The boundary, where `<=` and `<` disagree.

    The test above spans well past the cap, so it passes against either — the
    same weakness the gap-threshold test avoids by sitting exactly on its
    boundary.
    """
    step = B.RALLY_GAP_SECONDS  # no gap EXCEEDS the threshold, so step 1 keeps one group
    count = int(B.MAX_CLIP_DURATION / step) + 1
    times = [i * step for i in range(count)]
    assert times[-1] - times[0] == B.MAX_CLIP_DURATION

    segments = B.contact_segments(contacts_at(*times))
    assert [len(s) for s in segments] == [count], "a group exactly at the cap fits"


def test_the_split_falls_on_the_first_of_two_equal_gaps():
    """Which end the largest-gap scan counts from.

    With every gap identical, `>` keeps the first maximum and `>=` takes the
    last — and the two produce mirror-image segments of the same SIZES, so a
    test comparing lengths cannot tell them apart. This compares the contacts.
    """
    times = [i * B.RALLY_GAP_SECONDS for i in range(8)]
    segments = sorted(B.contact_segments(contacts_at(*times)), key=lambda s: s[0]["time"])

    assert [[c["time"] for c in seg] for seg in segments] == [
        [0.0],
        [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0],
    ]


def test_the_outer_cap_check_is_a_fast_path_for_the_inner_one():
    """Why mutating the OUTER `<=` to `<` changes nothing, and is not a gap.

    Step 2 tests the same condition twice: once before the split loop and once
    inside it. Loosening only the outer one sends a group that exactly fits into
    the loop, where the inner check immediately puts it back — identical output.
    A mutation run flags that as an untested line; it is an unreachable
    difference, and the two have to be broken together to change anything.

    Asserted rather than argued, so a future refactor that makes the two
    conditions differ fails here.
    """
    import inspect
    import re

    source = inspect.getsource(B.contact_segments)
    caps = re.findall(r"if span (<=?) MAX_CLIP_DURATION", source)
    assert caps == ["<=", "<="], f"the two cap checks must stay identical: {caps}"


def test_the_short_group_escape_hatch_is_unreachable_by_construction():
    """`len(grp) < 4` in step 2 cannot fire, and that is a fact about the constants.

    A group's gaps are all at most RALLY_GAP_SECONDS, so n contacts span at most
    (n-1) x GAP. Subdivision only runs when the span EXCEEDS
    MAX_CLIP_DURATION, which with fewer than four contacts would need
    3 x GAP > MAX. It is not — 15s against 30s — so the arm is dead.

    Pinned rather than deleted, because it stops being dead the moment someone
    lowers MAX_CLIP_DURATION, and a guard nobody can reach is exactly what a
    mutation run flags as untested code. This says why it is untested.
    """
    assert B.MAX_CLIP_DURATION > 3 * B.RALLY_GAP_SECONDS


def test_empty_in_empty_out():
    assert B.contact_segments([]) == []


def test_unsorted_input_is_sorted_before_grouping():
    segments = B.contact_segments(contacts_at(2.0, 0.0, 1.0))
    assert [c["time"] for c in segments[0]] == [0.0, 1.0, 2.0]


@pytest.mark.parametrize(
    "times",
    [
        (0.0, 1.0, 2.0),
        (0.0, 1.0, 2.0, 30.0, 31.0),
        (0.0, 1.0, 30.0),
        tuple(i * 1.0 for i in range(60)),
        (),
    ],
)
def test_the_extraction_did_not_move_the_rallies(times):
    """Equivalence against the segments the caller now receives.

    contacts_to_rallies must emit one rally per segment that clears both gates
    — which is the arithmetic the refactor could have broken and nothing else
    in ml/tests exercises directly.
    """
    contacts = contacts_at(*times)
    rallies = B.contacts_to_rallies(contacts, 600.0, 1080)
    survivors = [s for s in B.contact_segments(contacts) if len(s) >= B.MIN_RALLY_CONTACTS]
    kept = [
        s for s in survivors
        if min(600.0, s[-1]["time"] + B.POST_PLAY_PAD)
        - max(0.0, s[0]["time"] - B.PRE_RALLY_PAD) >= B.MIN_RALLY_DURATION
    ]
    assert len(rallies) == len(kept)


# ── Ladder: the accounting ───────────────────────────────────────────────────

def ladder(times_per_segment: list[int], duration: float = 600.0) -> Ladder:
    """One segment per entry, with that many contacts, spaced far enough apart."""
    times: list[float] = []
    t = 0.0
    for n in times_per_segment:
        for i in range(n):
            times.append(t + i * 0.5)
        t += (n * 0.5) + B.RALLY_GAP_SECONDS + 1.0
    return Ladder(contacts_at(*times), 1080, duration, normalize=True)


def test_the_deleted_band_is_everything_under_the_gate():
    lad = ladder([1, 2, 2, 5])
    assert lad.deleted_by_count == 3      # the 1 and both 2s
    assert lad.survive_count_gate == 1
    assert len(lad.rallies) == 1


def test_a_rally_lasting_exactly_the_minimum_is_kept():
    """The duration gate's own boundary, where `>=` and `>` disagree.

    Three contacts at the very start of a two-second video: `start` clamps to 0
    and `end` clamps to the video's length, so the clip is exactly
    MIN_RALLY_DURATION long. The gate test below uses a one-second video, where
    both comparisons agree — and this card's whole thesis is that this gate is a
    second cliff, so its boundary deserves the treatment the gap threshold got.
    """
    rallies = B.contacts_to_rallies(contacts_at(0.0, 0.05, 0.1), B.MIN_RALLY_DURATION, 1080)
    assert len(rallies) == 1
    assert rallies[0]["end"] - rallies[0]["start"] == B.MIN_RALLY_DURATION


def test_the_two_gates_are_counted_separately():
    # Three contacts inside a one-second video: it clears the count gate and
    # then dies on MIN_RALLY_DURATION, because `end` is clamped to the video's
    # length. Reporting this as a count-gate loss would send the fix at the
    # wrong threshold.
    lad = Ladder(contacts_at(0.0, 0.1, 0.2), 1080, 1.0, normalize=True)
    assert lad.deleted_by_count == 0
    assert lad.survive_count_gate == 1
    assert lad.deleted_by_duration == 1
    assert lad.rallies == []


def test_at_risk_counts_rallies_sitting_exactly_on_the_gate():
    lad = ladder([3, 3, 7])
    assert lad.at_risk == 2
    assert len(lad.rallies) == 3


def test_at_risk_does_not_count_a_rally_that_is_already_gone():
    """It is a subset of the rallies emitted, not of the segments.

    A three-contact segment killed by the duration gate is not a rally one
    detection from deletion, it is a rally already deleted. Counting it off the
    segment histogram reported it as the former — and could print an at-risk
    number LARGER than the rally count on the line above it, which is what gave
    this away.
    """
    lad = Ladder(contacts_at(0.0, 0.1, 0.2), 1080, 1.0, normalize=True)
    assert lad.rallies == []
    assert lad.at_risk == 0


def test_the_ladder_balances():
    lad = ladder([1, 2, 3, 4, 9])
    assert lad.deleted_by_count + lad.survive_count_gate == len(lad.segments)
    assert lad.survive_count_gate - lad.deleted_by_duration == len(lad.rallies)


# ── report: what a reader is told ────────────────────────────────────────────

def test_the_report_marks_the_deleted_band_and_names_both_gates():
    on = ladder([1, 2, 2, 3, 6])
    off = ladder([2, 3, 3, 4, 6])
    text = report(on, off, 1080, 3.0)
    assert "MIN_RALLY_CONTACTS" in text
    assert "one contact from deletion" in text
    assert "duration gate" in text


def test_the_report_says_when_the_fixture_cannot_show_the_cliff():
    # 360p: _scale_for is exactly 1.0, the two columns are the same run, and a
    # reader must not take "no cliff" from it. CF-375 (#475) is that gap.
    lad = ladder([3, 4])
    text = report(lad, lad, 360, 1.0)
    assert "cannot show the cliff" in text

    wide = report(ladder([3]), ladder([3, 4]), 1080, 3.0)
    assert "cannot show the cliff" not in wide
    assert "costs 1 rally on this fixture" in wide


def test_the_report_accounts_for_every_lost_rally():
    """Three channels, not two.

    Scaling can wipe out every contact in a rally, so the SEGMENT never forms —
    the same mechanism as the count gate, one step earlier, and a plausible
    large contributor at 1080p. With only the two gate rows the sentence
    silently dropped those, and the totals did not add up.
    """
    on = ladder([5])
    off = ladder([5, 5])
    text = report(on, off, 1080, 3.0)

    assert "costs 1 rally" in text
    assert "1 whose segment never formed" in text
    assert "0 to the count gate" in text
    assert "0 to the duration gate" in text


def test_a_360p_fixture_draws_no_conclusion_at_all():
    """The bottom line is the quotable one.

    At a scale of 1.0 the two columns are the same run, so "costs no rallies"
    reads as evidence that there is no cliff — the one thing this fixture
    cannot show. The note at the top said so and sat twenty lines above the
    conclusion.
    """
    lad = ladder([3, 4])
    text = report(lad, lad, 360, 1.0)

    assert "No conclusion available from this fixture" in text
    assert "costs no rallies" not in text
    assert "CF-375" in text, "and it names the card that fixes the gap"
