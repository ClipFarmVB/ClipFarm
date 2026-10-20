"""
Structural facts about find_contacts' speed gates (CF-103, #116).

The #116 threshold sweep concluded that CONTACT_HIT_SPEED_PXPS "isn't inert,
it's masked by the residual floor". This file pins the live half of that: once
the floor is down, the hit gate alone decides a marginal hit.

CONTACT_RESIDUAL_RATIO is deliberately not pinned as inert. Its threshold is
max(floor, ratio x speed_before), so it binds whenever speed_before exceeds
floor / ratio, and lowering it does admit contacts on fast balls: on the
fixtures with the switch off, and on a fast 360p ball. It looks flat only with
the switch on at 1080p, where the scaled floor puts the binding speed past
SEG_MAX_SPEED_PXPS — a fact about the shipping setting, not about the condition.

Relative to whatever CONTACT_HIT_SPEED_PXPS ships, never a value of it: the
stage-2 proposal to lower it to 220 was superseded by CF-376's soft scale
(#613), and a test naming a number would have to move with every retune.

(A third gate, MIN_SPEED_PXPS, was pinned here as subsumed-but-live. CF-174
went further and removed it as dead code — mutation-checked — so the class that
covered it is gone; test_ball_scaling.py now pins its absence instead.)

Worth having as a test and not only as a comment, because a masked knob
silently inverts the meaning of a sweep: a knob that cannot move is easy to
mistake for a knob that is correctly set, and that mistake is what shipped the
480 floor.

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
        """The gate CF-103 stage 2 swept: below it, no contact at all."""
        assert find_contacts(struck_track(CONTACT_HIT_SPEED_PXPS * 0.4), frame_height=360) == []
        assert len(find_contacts(struck_track(CONTACT_HIT_SPEED_PXPS * 2.0), frame_height=360)) == 1

    def test_lowering_the_gate_admits_a_previously_rejected_hit(self, restore_constants):
        """
        The knob is live once the residual floor is down: a hit just under the
        shipping threshold — its residual well clear of the floor, so the hit
        gate alone rejects it — comes back when the gate drops below it. That
        is the population a lower gate recovers, and the reason the knob is in
        SWEEPS and the LOO candidates at all.
        """
        marginal = struck_track(CONTACT_HIT_SPEED_PXPS * 0.96)
        assert find_contacts(marginal, frame_height=360) == []
        ball.CONTACT_HIT_SPEED_PXPS = CONTACT_HIT_SPEED_PXPS * 0.92
        assert len(find_contacts(marginal, frame_height=360)) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
