"""
CF-376 (#476): the soft contact-gate scale, `find_contacts(soft_scale=True)`.

The flag ships off, and off has to be `main` exactly: same contacts, same
labels, at every resolution, with or without the CF-174 switch. On, it gates on
`_scale_for(...) ** CONTACT_SOFT_SCALE_EXPONENT` and must change which contacts
exist without changing what any contact that was already found is called.

Each case below is built to be able to fail: the off-path cases also patch the
exponent to a value that would move every 1080p contact if it were read, and
the label cases assert the flag admitted something new before comparing.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.pipeline.ball as ball  # noqa: E402
from ml.pipeline.ball import (  # noqa: E402
    BallPosition,
    TrackedBall,
    _scale_for,
    classify_contact_action,
    find_contacts,
)

# Physical speeds in frame-heights/s, one per leg. They straddle both floors at
# 1080p: the full-scale floor is 0.667 fh/s and the softened one (e=0.5) is
# 0.385, so the 0.45 and 0.55 legs are the contacts the flag exists to admit.
# The closing pair of 0.30 legs is under both, and is there for the exponent
# patch below: an unscaled gate (exponent 0.0) admits it.
LEG_SPEEDS_FHPS = (0.30, 0.45, 0.55, 0.70, 0.90, 0.45, 1.00, 0.55, 0.30, 0.30)
# Direction of each leg (dx, dy), unit-ish. Mixed so the classifier sees more
# than one kind of redirect.
LEG_DIRECTIONS = ((1.0, 0.0), (-0.6, 0.8), (0.6, -0.8), (-1.0, 0.0),
                  (0.8, 0.6), (-0.8, -0.6), (1.0, 0.0), (-0.6, 0.8),
                  (1.0, 0.0), (-1.0, 0.0))


def rally(frame_height: int, step: float = 0.33, per_leg: int = 5) -> TrackedBall:
    """One coherent segment of several hits at the speeds above, rendered at
    `frame_height` — the same physical motion at every resolution."""
    positions = []
    x, y, t, frame = 0.5 * frame_height, 0.5 * frame_height, 0.0, 0
    for speed, (dx, dy) in zip(LEG_SPEEDS_FHPS, LEG_DIRECTIONS):
        v = speed * frame_height
        for _ in range(per_leg):
            positions.append(BallPosition(frame=frame, time=round(t, 4), x=x, y=y,
                                          confidence=0.8))
            x += dx * v * step
            y += dy * v * step
            t += step
            frame += 10
    return TrackedBall(positions=positions)


def reversal(frame_height: int, speed_pxps: float) -> TrackedBall:
    """One straight leg out and back at `speed_pxps`: a single redirect, so the
    track holds exactly one candidate contact, gated at that speed."""
    positions, x = [], 0.2 * frame_height
    for i in range(16):
        positions.append(BallPosition(frame=i, time=i * 0.33, x=x, y=0.5 * frame_height,
                                      confidence=0.8))
        x += (1.0 if i < 8 else -1.0) * speed_pxps * 0.33
    return TrackedBall(positions=positions)


def run(frame_height: int, **kw) -> str:
    """find_contacts output as bytes-comparable JSON."""
    return json.dumps(find_contacts(rally(frame_height), frame_height=frame_height, **kw),
                      sort_keys=True)


@pytest.fixture(autouse=True)
def _quiet(caplog):
    # 2160p is past the clamp and logs at error on every call; not under test.
    caplog.set_level(logging.CRITICAL, logger="ml.pipeline.ball")


class TestOffIsMain:
    @pytest.mark.parametrize("height", [360, 1080, 2160])
    @pytest.mark.parametrize("normalize", [True, False])
    def test_off_is_the_default_and_is_byte_identical(self, height, normalize):
        assert run(height, normalize=normalize) == run(
            height, normalize=normalize, soft_scale=False)

    @pytest.mark.parametrize("height", [360, 1080, 2160])
    def test_off_never_reads_the_exponent(self, monkeypatch, height):
        """Byte-identical to the unpatched run with the exponent set to 0.0, a
        value that turns every scale into 1.0 if anything consults it."""
        before = run(height)
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", 0.0)
        assert run(height) == before
        assert run(height, soft_scale=False) == before

    def test_that_patch_would_be_seen_if_the_exponent_were_read(self, monkeypatch):
        """Non-vacuity for the case above: with the flag on, the same patch does
        move the 1080p output."""
        before = run(1080, soft_scale=True)
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", 0.0)
        assert run(1080, soft_scale=True) != before


class TestNoOpWhereTheScaleIsOne:
    """1.0 ** e == 1.0, so wherever _scale_for returns exactly 1.0 the flag
    cannot move anything. Patched to 0.25 to make any leak large."""

    @pytest.mark.parametrize("exponent", [0.5, 0.25])
    def test_360p(self, monkeypatch, exponent):
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", exponent)
        assert run(360, soft_scale=True) == run(360)

    def test_unknown_height(self, monkeypatch):
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", 0.25)
        assert run(0, soft_scale=True) == run(0)

    def test_past_the_clamp_point(self, monkeypatch):
        """The revert is decided on the full scale, before softening.

        The probe leg sits between the unscaled floor and the floor a gate
        would use if softening ran *before* the clamp (2160/360 = 6.0, raised
        to the patched 0.25, is ~1.57, which never reaches the cap and so never
        reverts). Correct code admits it either way; softening first would
        reject it with the flag on."""
        exponent = 0.25
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", exponent)
        height = 2160
        assert _scale_for(height, log=False) == 1.0
        unscaled_floor = ball.CONTACT_HIT_SPEED_PXPS
        soft_first_floor = ball.CONTACT_HIT_SPEED_PXPS * (
            height / ball.REFERENCE_FRAME_HEIGHT) ** exponent
        speed = (unscaled_floor + soft_first_floor) / 2
        assert unscaled_floor < speed < soft_first_floor
        track = reversal(height, speed)
        hard = find_contacts(track, frame_height=height)
        assert len(hard) == 1, "the probe must clear the reverted gate"
        assert find_contacts(track, frame_height=height, soft_scale=True) == hard
        assert run(height, soft_scale=True) == run(height)

    def test_with_the_cf174_switch_off(self, monkeypatch):
        """The flag only has an effect while ball_contact_scale_enabled is on."""
        monkeypatch.setattr(ball, "CONTACT_SOFT_SCALE_EXPONENT", 0.25)
        for height in (720, 1080, 1440):
            assert run(height, normalize=False, soft_scale=True) == run(
                height, normalize=False)

    def test_360p_track_is_the_same_set_the_ticket_measured(self):
        """test1 (360p) is unmoved by construction; the pin is the property
        that makes that so, not an eval run."""
        assert _scale_for(360, log=False) == 1.0
        assert 1.0 ** ball.CONTACT_SOFT_SCALE_EXPONENT == 1.0


class TestOnChangesWhichContactsExistNotWhatTheyAreCalled:
    @pytest.mark.parametrize("height", [720, 1080, 1440])
    def test_already_admitted_contacts_keep_their_labels(self, height):
        hard = find_contacts(rally(height), frame_height=height)
        soft = find_contacts(rally(height), frame_height=height, soft_scale=True)
        assert len(soft) > len(hard), "the flag has to admit something here"
        by_frame = {c["frame"]: c for c in soft}
        shared = [c for c in hard if c["frame"] in by_frame]
        assert shared, "no contact is found both ways, so nothing is compared"
        for c in shared:
            assert by_frame[c["frame"]] == c, (
                f"contact at frame {c['frame']} changed under the flag"
            )

    def test_newly_admitted_contacts_are_judged_on_the_full_scale(self):
        """The classifier keeps dividing by _scale_for, not by the softened
        gate scale. rally() is one segment, so its indices are the segment's."""
        height = 1080
        track = rally(height)
        hard = {c["frame"] for c in find_contacts(track, frame_height=height)}
        new = [c for c in find_contacts(track, frame_height=height, soft_scale=True)
               if c["frame"] not in hard]
        assert new, "the flag has to admit something here"
        index = {p.frame: i for i, p in enumerate(track.positions)}
        for c in new:
            i = index[c["frame"]]
            assert (c["action"], c["action_confidence"]) == classify_contact_action(
                track.positions, i, height, normalize=True)

    def test_the_gates_are_the_full_ones_raised_to_the_exponent(self, caplog):
        caplog.set_level(logging.INFO, logger="ml.pipeline.ball")
        find_contacts(rally(1080), frame_height=1080, soft_scale=True)
        floor = ball.CONTACT_HIT_SPEED_PXPS * 3.0 ** ball.CONTACT_SOFT_SCALE_EXPONENT
        assert f"hit_speed {floor:.0f}" in caplog.text

    def test_below_360p_soft_is_stricter_not_looser(self):
        """Documented in ball.py: the softened scale sits nearer 1.0 on both
        sides, so under 360p the gates rise. The probe speed sits midway between
        the full 240p floor and the softened one (160 and ~196 px/s at today's
        240 px/s constant), derived from the constant so a retune keeps it
        between them."""
        height = 240
        full = _scale_for(height, log=False)
        assert full < 1.0
        full_floor = ball.CONTACT_HIT_SPEED_PXPS * full
        soft_floor = ball.CONTACT_HIT_SPEED_PXPS * full ** ball.CONTACT_SOFT_SCALE_EXPONENT
        speed = (full_floor + soft_floor) / 2
        assert full_floor < speed < soft_floor
        track = reversal(height, speed)
        assert len(find_contacts(track, frame_height=height)) == 1
        assert find_contacts(track, frame_height=height, soft_scale=True) == []
