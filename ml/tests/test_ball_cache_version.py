"""Changing how tracking works must change the cache key (CF-231 #238, CF-229 #233).

Cached ball tracks are keyed on the video's md5, the model and `sample_every`.
Nothing else — so a change to any OTHER tracking input leaves every existing
entry looking valid, and the next run of an already-processed video replays a
track built by the old code.

That failure is quiet in the worst way. On a tuning change it is a wrong answer
nobody can see. On a *re-measure* it is worse: the numbers come back unchanged,
which reads as "the change had no effect" rather than "the change never ran" —
and both #238 and #233 are cards whose whole deliverable is a re-measure.

Both name this as a prerequisite, and say whichever lands first should do it.
So `TRACKING_CACHE_VERSION` exists, and this is what makes bumping it a
decision rather than something to remember: the constants below are fingerprinted,
and moving one without moving the version fails here.

**What is in the fingerprint is the argument.** Only what `track_ball` reads,
because the cache holds RAW POSITIONS. The segmentation and contact constants
run afterwards, on those positions, so folding them in would orphan every
cached track for a change that cannot move one — which is not free: a bump
means the next run of every video re-tracks, on Modal, for real money.
"""
import hashlib

import pytest

from ml.pipeline import ball as B

# The inputs `track_ball` reads, minus the two already in the key (MODEL_ID and
# SAMPLE_EVERY). Adding a tracking input means adding it here.
TRACK_SHAPING_CONSTANTS = ("MIN_CONF", "MAX_JUMP_PX", "MAX_MISS")

# Recomputed and pasted in when the version is bumped. Not derived at import —
# a fingerprint that follows the code it guards guards nothing.
EXPECTED_FINGERPRINT = "4475fb945d957f21"


def fingerprint() -> str:
    joined = "|".join(f"{name}={getattr(B, name)!r}" for name in TRACK_SHAPING_CONSTANTS)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def test_the_track_shaping_constants_have_not_moved_without_a_version_bump():
    assert fingerprint() == EXPECTED_FINGERPRINT, (
        "One of "
        f"{', '.join(TRACK_SHAPING_CONSTANTS)} changed, so a given video now "
        "produces a DIFFERENT track — and every cached one was built by the old "
        "code.\n"
        "Bump ball.TRACKING_CACHE_VERSION and paste the new fingerprint here, in "
        "the same PR.\n"
        f"  new fingerprint: {fingerprint()}\n"
        "Bumping orphans every cached track, so the next run of every video "
        "re-tracks on Modal. Do it because the track changed, not to be safe."
    )


def test_every_constant_in_the_fingerprint_exists():
    """A typo would silently drop a constant out of the guard."""
    for name in TRACK_SHAPING_CONSTANTS:
        assert hasattr(B, name), f"ball.{name} is gone; the fingerprint is stale"


def test_the_fingerprint_actually_moves_when_a_constant_does(monkeypatch):
    """The guard is only worth having if it fires.

    A fingerprint that happened to ignore its inputs would pass forever, which
    is exactly the shape of the problem it exists to catch.
    """
    before = fingerprint()
    monkeypatch.setattr(B, "MAX_JUMP_PX", B.MAX_JUMP_PX + 1)
    assert fingerprint() != before


def test_the_version_is_an_int_that_can_only_go_up():
    # A string, a float or a date would still key correctly and would make
    # "has this been bumped?" a judgement call in review.
    assert isinstance(B.TRACKING_CACHE_VERSION, int)
    assert B.TRACKING_CACHE_VERSION >= 1


def test_the_segmentation_constants_are_deliberately_not_in_the_fingerprint():
    """They run AFTER the cache, on the positions it holds.

    Including them would orphan every cached track for a change that cannot
    move one — and a bump costs a full re-track of every video. Stated as a
    test because "why isn't SEG_MAX_SPEED_PXPS in here" is the obvious question
    and the answer is not obvious.
    """
    for name in ("SEG_MAX_SPEED_PXPS", "SEG_MIN_MEDIAN_SPEED_PXPS",
                 "CONTACT_HIT_SPEED_PXPS", "MIN_RALLY_CONTACTS"):
        assert hasattr(B, name), f"ball.{name} moved; revisit this test"
        assert name not in TRACK_SHAPING_CONSTANTS


@pytest.mark.parametrize("name", TRACK_SHAPING_CONSTANTS)
def test_each_fingerprinted_constant_is_read_by_track_ball(name):
    """The fingerprint must describe what tracking actually uses.

    A constant that stopped shaping the track would keep forcing re-tracks for
    nothing; one that started shaping it and was never added is the bug this
    file exists for. Read from the source of `track_ball` and the helper it
    delegates to.
    """
    import inspect

    source = inspect.getsource(B.track_ball) + inspect.getsource(B._pick_active)
    if name == "MIN_CONF":
        # Applied in the detection helper track_ball calls per frame.
        source += inspect.getsource(B._detect_frame)
    assert name in source, f"{name} is fingerprinted but nothing in tracking reads it"
