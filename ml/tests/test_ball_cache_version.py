"""Changing how tracking works must change the cache key (CF-231 #238, CF-229 #233).

Cached ball tracks are keyed on the video's md5, the model and `sample_every`.
Nothing else — so a change to any OTHER tracking input leaves every existing
entry looking valid, and the next run of an already-processed video replays a
track built by the old code.

That failure is quiet in the worst way. On a tuning change it is a wrong answer
nobody can see. On a *re-measure* it is worse: the numbers come back unchanged,
which reads as "the change had no effect" rather than "the change never ran" —
and both #238 and #233 end in a re-measure.

#238 names this as a prerequisite, and says CF-229 (#233) has the same one and
that whichever lands first should do it.
So `TRACKING_CACHE_VERSION` exists, and this is what makes bumping it a
decision rather than something to remember: the constants below are fingerprinted,
and moving one without moving the version fails here.

**What is in the fingerprint is the argument.** Only what `track_ball` reads,
because the cache holds RAW POSITIONS. The segmentation and contact constants
run afterwards, on those positions, so folding them in would orphan every
cached track for a change that cannot move one — which is not free: a bump
means the next run of every video re-tracks, on Modal, for real money.

**Values are not enough, so the CODE is fingerprinted too.** A first version of
this hashed three constants and nothing else, which missed both cards it was
written for. This repo's idiom for "scale a threshold" is to scale AT USE and
leave the constant alone — `_scale_for` / `REFERENCE_FRAME_HEIGHT`, the CF-174
precedent. A CF-229 written that way leaves `MAX_JUMP_PX = 300` untouched, and
#238's downscale adds a new input that a hand-written tuple simply would not
mention. Both would have shipped with every cached track silently stale.

The source is normalized through `ast` before hashing, so a reformat or a
comment edit is not a false bump; a changed expression is.
"""
import ast
import hashlib
import inspect
import sys
import textwrap

import pytest

from ml.pipeline import ball as B

# The inputs `track_ball` reads, minus MODEL_ID, which is in the key already.
#
# SAMPLE_EVERY is NOT in the key, despite an earlier comment here saying it was.
# What the key holds is the `sample_every` ARGUMENT, which production computes
# per video (`max(1, round(fps / 3.0))` in tasks.py and all three eval entry
# points). The module constant survives in the production path as the
# DENOMINATOR of `max_jump = MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)`, so
# moving it changes the emitted track while every key stays byte-identical.
TRACK_SHAPING_CONSTANTS = ("MIN_CONF", "MAX_JUMP_PX", "MAX_MISS", "SAMPLE_EVERY")

# The code that turns a video into raw positions. An edit to any of it changes
# the track whether or not a constant moved. `TrackedBall` is here because
# `_pick_active` chooses among detections by `tracker.predict_next(frame)`, so
# changing that extrapolation changes which detection wins; `_load_model` builds
# the model `_detect_frame` infers with.
TRACK_SHAPING_CODE = ("track_ball", "_pick_active", "_detect_frame", "TrackedBall", "_load_model")

# One fingerprint per TRACKING_CACHE_VERSION, appended when the version is
# bumped. Keyed by version so a new fingerprint has nowhere to go but a new
# version: adding one without the bump fails the key check, and pasting one over
# an existing entry fails the pin below. Not derived at import — a fingerprint
# that follows the code it guards guards nothing.
RECORDED_FINGERPRINTS = {1: "8cd501ea88362efb"}

# The same entries a second time. Rewriting a recorded fingerprint then means
# editing two places, one of which says not to, rather than pasting one line.
PINNED_FINGERPRINTS = {1: "8cd501ea88362efb"}

# Fingerprints are recorded on the interpreter CI runs: `CLAUDE.md` requires
# 3.11, which is what `ci.yml` and `Dockerfile.api` run. They hash `ast.dump`
# output, and the first review round on this PR measured a different fingerprint
# from the same source on 3.12 and on 3.13, so a mismatch elsewhere says nothing
# about the track.
RECORDED_ON = (3, 11)


def _normalized_source(func) -> str:
    """A function's structure, with formatting, comments and docstring dropped.

    `ast.dump` of the parsed tree: an expression change moves it, `black`
    rewrapping a line or someone rewriting a comment does not. Without this the
    guard would cry wolf on every reformat, and a guard that fires on no-ops
    gets bumped past reflexively — which is exactly the habit it exists to stop.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def fingerprint() -> str:
    joined = "|".join(f"{name}={getattr(B, name)!r}" for name in TRACK_SHAPING_CONSTANTS)
    joined += "|" + "|".join(
        _normalized_source(getattr(B, name)) for name in TRACK_SHAPING_CODE
    )
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def test_the_track_shaping_inputs_have_not_moved_without_a_version_bump():
    if sys.version_info[:2] != RECORDED_ON:
        pytest.skip(
            f"fingerprints are recorded on Python {RECORDED_ON[0]}.{RECORDED_ON[1]}, "
            f"which CI runs; `ast.dump` output may differ on {sys.version.split()[0]}, "
            "so a mismatch here would say nothing about the track"
        )
    version = B.TRACKING_CACHE_VERSION
    now = fingerprint()
    assert RECORDED_FINGERPRINTS.get(version) == now, (
        "What shapes a track changed: one of "
        f"{', '.join(TRACK_SHAPING_CONSTANTS)}, or the code of "
        f"{', '.join(TRACK_SHAPING_CODE)}.\n"
        "If a given video now produces a DIFFERENT track, every cached one was "
        f"built by the old code: set ball.TRACKING_CACHE_VERSION = {version + 1} and "
        f"add `{version + 1}: \"{now}\"` to RECORDED_FINGERPRINTS and "
        "PINNED_FINGERPRINTS, in the same PR, and at release run "
        "`modal deploy ml/modal_app.py` BEFORE deploying the worker. Until that "
        "deploy, do not run `ml.eval.harness --offline` or `diagnose_detection` "
        "with the bumped version: they key off the local `ml` but track on the "
        "deployed Modal app.\n"
        "If the edit cannot change a track (a log line, progress reporting), "
        f"replace version {version}'s entry in both instead, and say why in the PR.\n"
        "Bumping orphans every cached track, so the next run of every video "
        "re-tracks on Modal. Do it because the track changed, not to be safe."
    )


def test_each_version_has_exactly_one_recorded_fingerprint():
    """A new fingerprint needs a new version: the keys are 1..TRACKING_CACHE_VERSION."""
    assert sorted(RECORDED_FINGERPRINTS) == list(range(1, B.TRACKING_CACHE_VERSION + 1)), (
        f"RECORDED_FINGERPRINTS covers versions {sorted(RECORDED_FINGERPRINTS)}, but "
        f"ball.TRACKING_CACHE_VERSION is {B.TRACKING_CACHE_VERSION}: a fingerprint was "
        "added without bumping the version, or the version was bumped without one"
    )


def test_a_recorded_fingerprint_is_not_rewritten_by_pasting_over_it():
    """Pasting a new fingerprint over the current entry is the shortcut past a bump.

    The key check cannot see it, since the keys do not change. So each entry is
    pinned a second time: rewriting one stays possible, but it is then a
    decision written down in two places rather than the one-line paste a
    failure message invites.
    """
    assert set(PINNED_FINGERPRINTS) == set(RECORDED_FINGERPRINTS), (
        f"RECORDED_FINGERPRINTS covers versions {sorted(RECORDED_FINGERPRINTS)} but "
        f"PINNED_FINGERPRINTS covers {sorted(PINNED_FINGERPRINTS)}: a recorded version "
        "without its pin can later be pasted over unseen"
    )
    for version, pinned in PINNED_FINGERPRINTS.items():
        assert RECORDED_FINGERPRINTS.get(version) == pinned, (
            f"RECORDED_FINGERPRINTS[{version}] no longer matches its pin. If the track "
            "changed, add a new version rather than rewriting this one."
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
    file exists for.

    Resolved by walking the ast for `Name` nodes, not by searching the source
    text. A substring scan reads comments and docstrings too, so replacing
    `confidence=MIN_CONF` with `confidence=0.40  # MIN_CONF` left the constant
    genuinely unread and this test still passing — against the one case its own
    docstring names.
    """
    read = set()
    for code_name in TRACK_SHAPING_CODE:
        tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(B, code_name))))
        read |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert name in read, (
        f"{name} is fingerprinted but no tracking code reads it — either it "
        "stopped shaping the track (drop it, and bump the version) or it moved "
        "somewhere TRACK_SHAPING_CODE does not cover"
    )
