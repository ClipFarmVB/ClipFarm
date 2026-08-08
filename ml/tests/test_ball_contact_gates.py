"""
Structural facts about find_contacts' speed gates (CF-103, #116).

The #116 threshold sweep concluded that CONTACT_RESIDUAL_RATIO and
CONTACT_HIT_SPEED_PXPS "aren't inert, they're masked by the residual floor". The
multi-fixture re-run (ml/eval/tune_contacts.py) showed that was only half right:
one of them really does move contacts once the floor is down, the other two are
no-ops for reasons that live in the *shape* of the conditions rather than in any
fixture. Those reasons are what this file pins.

Worth having as tests and not only as comments, because both facts silently
invert the meaning of a sweep: a knob that cannot move is easy to mistake for a
knob that is correctly set, and that mistake is what shipped the 480 floor.

ml/pipeline/ball.py imports OpenCV lazily, so this runs with numpy alone —
matching what the ml/tests CI job installs.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.pipeline.ball as ball  # noqa: E402
from ml.pipeline.ball import (  # noqa: E402
    CONTACT_HIT_SPEED_PXPS,
    MIN_SPEED_PXPS,
    BallPosition,
    TrackedBall,
    find_contacts,
)


@pytest.fixture
def restore_constants():
    """Undo module-constant patching — find_contacts reads these at call time."""
    saved = {name: getattr(ball, name) for name in dir(ball) if name.isupper()}
    yield
    for name, value in saved.items():
        setattr(ball, name, value)


def struck_track(speed_pxps: float, frame_height: int = 360,
                 step: float = 0.33, hit_at: int = 8, n: int = 16) -> TrackedBall:
    """A ball flying right at speed_pxps, reversed at `hit_at` — one clean contact."""
    positions, x, y = [], 0.2 * frame_height, 0.5 * frame_height
    for i in range(n):
        positions.append(BallPosition(
            frame=int(i * step * 30), time=i * step, x=x, y=y, confidence=0.8,
        ))
        x += (1.0 if i < hit_at else -1.0) * speed_pxps * step
    return TrackedBall(positions=positions)


class TestHitSpeedGate:
    def test_gate_rejects_a_ball_slower_than_the_threshold(self):
        """The gate that CF-103 stage 2 retuned: below it, no contact at all."""
        assert find_contacts(struck_track(CONTACT_HIT_SPEED_PXPS * 0.4), frame_height=360) == []
        assert len(find_contacts(struck_track(CONTACT_HIT_SPEED_PXPS * 2.0), frame_height=360)) == 1

    def test_lowering_the_gate_admits_a_previously_rejected_hit(self, restore_constants):
        """
        Directional guard for the 240 -> 220 change. A hit just under the old
        threshold is exactly the population that move recovered: on the labeled
        fixtures it was 4 whole rallies that had been producing zero contacts.
        """
        marginal = struck_track(230.0)
        ball.CONTACT_HIT_SPEED_PXPS = 240.0
        assert find_contacts(marginal, frame_height=360) == []
        ball.CONTACT_HIT_SPEED_PXPS = 220.0
        assert len(find_contacts(marginal, frame_height=360)) == 1


class TestMinSpeedIsSubsumed:
    def test_min_speed_gate_cannot_fire_below_the_hit_speed_gate(self):
        """
        MIN_SPEED_PXPS needs *both* sides slow; the hit-speed gate needs only the
        faster side slow. So while CONTACT_HIT_SPEED_PXPS >= MIN_SPEED_PXPS,
        anything the first would reject the second has already rejected, and the
        sweep measures a flat line at every value. Pinned so a future tuner reads
        that flat line as "subsumed", not as "correctly set".
        """
        assert CONTACT_HIT_SPEED_PXPS >= MIN_SPEED_PXPS

    @pytest.mark.parametrize("min_speed", [240.0, 180.0, 120.0, 60.0, 0.0])
    def test_sweeping_min_speed_changes_nothing(self, restore_constants, min_speed):
        ball.MIN_SPEED_PXPS = min_speed
        contacts = find_contacts(struck_track(0.9 * 360), frame_height=360)
        assert [c["time"] for c in contacts] == [8 * 0.33]

    def test_it_does_bite_once_pushed_above_the_hit_speed_gate(self, restore_constants):
        """Not dead code — it is the guard that starts working if hit speed drops."""
        ball.MIN_SPEED_PXPS = CONTACT_HIT_SPEED_PXPS * 4
        assert find_contacts(struck_track(0.9 * 360), frame_height=360) == []


class TestResidualRatioIsOneSided:
    """
    max(floor, ratio x speed_before) means the ratio only ever *raises* the
    threshold. Lowering it below the shipping 0.5 is a measured no-op on all four
    fixtures; raising it removes contacts. The #116 sweep saw the flat half and
    read it as masking.
    """

    @pytest.mark.parametrize("ratio", [0.50, 0.35, 0.25, 0.15, 0.0])
    def test_lowering_the_ratio_changes_nothing(self, restore_constants, ratio):
        ball.CONTACT_RESIDUAL_RATIO = ratio
        contacts = find_contacts(struck_track(0.9 * 360), frame_height=360)
        assert [c["time"] for c in contacts] == [8 * 0.33]

    def test_raising_the_ratio_does_reject_a_contact(self, restore_constants):
        ball.CONTACT_RESIDUAL_RATIO = 4.0
        assert find_contacts(struck_track(0.9 * 360), frame_height=360) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
