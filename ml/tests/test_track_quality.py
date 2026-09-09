"""CF-229 (#233): the track-quality instrument, and the unit bug it is about.

Two things here, and they are not the same thing.

`track_quality` is a measurement tool — it changes no threshold. It is tested
over hand-built tracks with known speeds, because the whole point is that the
numbers it prints are the ones CF-229's acceptance is written in and nobody can
currently produce them.

The guard at the bottom is about `MAX_JUMP_PX`, which is a PIXEL constant
corrected for the sampling interval and not for frame height. That is the same
class of unit bug CF-174 fixed for the px/s constants, `ball.py` records it as a
deliberate compromise rather than an oversight, and nothing enforced it — so
scaling it would have looked like an ordinary change. CF-229 names it as
something to consider in the same pass; this makes that a decision with a diff.
"""
import pytest

pytest.importorskip("numpy")

from ml.eval.track_quality import (  # noqa: E402
    STATIONARY_PXPS,
    consecutive_speeds,
    fraction,
    report,
)
from ml.pipeline import ball as B  # noqa: E402


def track(*speeds_pxps: float, dt: float = 0.1):
    """A track whose consecutive speeds are exactly `speeds_pxps`."""
    x = 0.0
    positions = [B.BallPosition(frame=0, time=0.0, x=0.0, y=0.0, confidence=1.0)]
    for i, v in enumerate(speeds_pxps, start=1):
        x += v * dt
        positions.append(
            B.BallPosition(frame=i, time=i * dt, x=x, y=0.0, confidence=1.0)
        )
    return B.TrackedBall(positions=positions)


def test_the_speeds_are_the_ones_between_consecutive_samples():
    samples = consecutive_speeds(track(100.0, 500.0, 0.0))
    assert [round(s.pxps) for s in samples] == [100, 500, 0]


def test_a_detection_gap_contributes_no_sample_rather_than_a_huge_jump():
    """The rule `_segment_track` and the condense bridge both apply.

    Counting a pair that spans a dropout would put a fabricated teleport into
    every distribution this tool prints — and it is exactly the shape of the
    number the tool exists to measure.
    """
    positions = [
        B.BallPosition(frame=0, time=0.0, x=0.0, y=0.0, confidence=1.0),
        B.BallPosition(frame=1, time=0.1, x=10.0, y=0.0, confidence=1.0),
        # A gap longer than MAX_SAMPLE_GAP_SEC, then the ball reappears far away.
        B.BallPosition(
            frame=99, time=0.1 + B.MAX_SAMPLE_GAP_SEC + 0.5, x=9000.0, y=0.0,
            confidence=1.0,
        ),
    ]
    samples = consecutive_speeds(B.TrackedBall(positions=positions))
    assert len(samples) == 1, "the pair spanning the gap must be dropped"
    assert round(samples[0].pxps) == 100


def test_a_zero_or_backwards_timestamp_is_dropped_rather_than_dividing_by_zero():
    positions = [
        B.BallPosition(frame=0, time=1.0, x=0.0, y=0.0, confidence=1.0),
        B.BallPosition(frame=1, time=1.0, x=50.0, y=0.0, confidence=1.0),
    ]
    assert consecutive_speeds(B.TrackedBall(positions=positions)) == []


def test_the_lock_on_fraction_is_the_number_the_card_asks_for():
    """CF-229: "test4's ~0 px/s sample fraction materially below 50%"."""
    # Six samples, three of them stationary.
    samples = consecutive_speeds(track(0.0, 1.0, 2.0, 400.0, 500.0, 600.0))
    assert fraction(samples, lambda v: v < STATIONARY_PXPS) == pytest.approx(0.5)


def test_the_stationary_band_sits_above_detector_jitter_not_at_zero():
    """A locked-on detection wanders a pixel or two; an exact zero misses it.

    The segmentation config puts detector jitter at ~0-5 px/s at any
    resolution, so the band has to clear that and stay well under the
    held-ball filter, or it stops describing the thing it is named after.
    """
    assert 5.0 < STATIONARY_PXPS < B.SEG_MIN_MEDIAN_SPEED_PXPS


def test_the_report_shows_both_ceilings_and_says_they_move_together():
    text = report(track(0.0, 0.0, 800.0, 5000.0), frame_height=1080)
    assert "THE LOCK-ON SIGNATURE" in text
    # The shipped ceiling AND the scaled one's actual value, so the reader can
    # see the trade rather than being told it. Asserting only the label left a
    # version that printed the unscaled number twice looking identical.
    assert f"{B.SEG_MAX_SPEED_PXPS:.0f} px/s (shipped, unscaled)" in text
    scaled = B.SEG_MAX_SPEED_PXPS * (1080 / B.REFERENCE_FRAME_HEIGHT)
    assert scaled != B.SEG_MAX_SPEED_PXPS, "1080p must not be the reference height"
    assert f"over {scaled:.0f} px/s (if it scaled)" in text
    assert "Both numbers, or neither" in text


def test_the_report_refuses_to_claim_it_can_simulate_the_jump_threshold():
    """The limitation that decides what this tool is worth.

    `MAX_JUMP_PX` picks among the detections a frame returned, and a dump holds
    only the ones that won — so no tool reading a dump can answer it. Saying so
    in the output, not only in the docstring, because the row above it looks
    exactly like an answer.
    """
    text = report(track(100.0, 200.0), frame_height=1080)
    assert "NOT a simulation of" in text
    assert "needs a re-track" in text


def test_an_empty_dump_says_so_rather_than_dividing_by_zero():
    assert "no usable consecutive samples" in report(track(), frame_height=1080)


# ── the unit bug the card names ──────────────────────────────────────────────

def test_the_jump_threshold_is_corrected_for_sampling_and_not_for_frame_height():
    """`MAX_JUMP_PX` is a PIXEL budget and is normalized by neither the right
    thing nor nothing — it is corrected for time and not for frame size.

    `track_ball` computes `MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)`: more
    frames skipped means more elapsed time means more pixels of legitimate
    travel. Correct, and it leaves the CF-174 bug in place — the same physical
    jump covers ~3x more pixels at 1080p, so this admits a third of the
    physical distance there that it admits at 360p.

    `ball.py` records that as a deliberate compromise and nothing enforced it.
    CF-229 lists scaling it as something to consider in the same pass as
    SEG_MAX_SPEED_PXPS, and it is the tracking layer, upstream of everything —
    so it should change as a decision with a diff, not as an ordinary-looking
    line. Reading the source rather than the value, because the value is what a
    change would alter.
    """
    import inspect
    import re

    source = inspect.getsource(B.track_ball)
    assignment = re.search(r"^\s*max_jump\s*=\s*(.+)$", source, re.M)
    assert assignment, "track_ball no longer computes max_jump where this expects it"
    expression = assignment.group(1)

    assert "sample_every" in expression, "the sampling correction must stay"
    assert "frame_height" not in expression and "REFERENCE_FRAME_HEIGHT" not in expression, (
        "max_jump is now frame-height scaled. That is CF-229's change and it is "
        "welcome — update this test in the same PR, with the re-measured "
        "dead-time numbers the card asks for and test1 unmoved."
    )
