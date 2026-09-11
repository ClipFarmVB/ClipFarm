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
    assert fraction(samples, lambda s: s.pxps < STATIONARY_PXPS) == pytest.approx(0.5)


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

def _max_jump_expression():
    """The `max_jump = ...` expression from track_ball, as source text.

    An ast walk of the whole function body rather than a regex over one line.
    The regex version matched the assignment on a single line only, so
    wrapping it across two — a pure reformat — captured `(` and failed
    with "the sampling correction must stay", while a genuine change on a
    second line went unseen.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(B.track_ball)))
    found = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "max_jump" for t in node.targets)
    ]
    assert len(found) == 1, (
        f"expected exactly one `max_jump = ...` in track_ball, found {len(found)}"
    )
    return ast.unparse(found[0])


def _eval_max_jump(expression: str, *, sample_every: int, frame_height: int) -> float:
    """Evaluate that expression against synthetic inputs.

    Everything `track_ball` could legitimately reach for is in scope, so an
    expression that starts reading a new constant evaluates rather than raising
    NameError — the point is to catch a changed VALUE, not a changed spelling.
    """
    scope = {name: getattr(B, name) for name in dir(B) if name.isupper()}
    scope.update(sample_every=sample_every, frame_height=frame_height)
    return float(eval(expression, {"__builtins__": {}}, scope))  # noqa: S307


def test_the_jump_threshold_is_corrected_for_sampling_and_not_for_frame_height():
    """`MAX_JUMP_PX` is a PIXEL budget corrected for time and not for frame size.

    `track_ball` computes `MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)`: more
    frames skipped means more elapsed time means more pixels of legitimate
    travel. Correct, and it leaves the CF-174 bug in place — the same physical
    jump covers ~3x more pixels at 1080p, so this admits a third of the
    physical distance there that it admits at 360p.

    `ball.py` records that as a deliberate compromise and nothing enforced it.
    CF-229 lists scaling it as something to consider in the same pass as
    SEG_MAX_SPEED_PXPS, and it is the tracking layer, upstream of everything —
    so it should change as a decision with a diff, not as an ordinary-looking
    line.

    Asserted on the VALUE the expression produces, not on the words in it. The
    first version of this test scraped the source line for the substrings
    "sample_every" and "frame_height", which let `MAX_JUMP_PX * sample_every`
    through — 3x more permissive at the shipped cadence — because the substring
    was still present, and let a frame-height scale through on an adjacent line.
    """
    expression = _max_jump_expression()

    # At the shipped cadence the correction is a no-op, so the budget is the
    # constant itself. This is the anchor: a factor that scales with
    # sample_every but has the wrong magnitude fails here.
    at_shipped = _eval_max_jump(expression, sample_every=B.SAMPLE_EVERY, frame_height=360)
    assert at_shipped == pytest.approx(B.MAX_JUMP_PX), (
        "at sample_every == SAMPLE_EVERY the jump budget must be exactly "
        f"MAX_JUMP_PX ({B.MAX_JUMP_PX}), got {at_shipped}"
    )

    # Twice the stride, twice the elapsed time, twice the pixels.
    doubled = _eval_max_jump(expression, sample_every=B.SAMPLE_EVERY * 2, frame_height=360)
    assert doubled == pytest.approx(at_shipped * 2), (
        "the jump budget must scale linearly with sample_every; "
        f"{B.SAMPLE_EVERY} -> {at_shipped}, {B.SAMPLE_EVERY * 2} -> {doubled}"
    )

    # The CF-229 change, when it comes, moves this number.
    at_1080 = _eval_max_jump(expression, sample_every=B.SAMPLE_EVERY, frame_height=1080)
    assert at_1080 == pytest.approx(at_shipped), (
        "max_jump is now frame-height scaled. That is CF-229's change and it is "
        "welcome — update this test in the same PR, with the re-measured "
        "dead-time numbers the card asks for and test1 unmoved."
    )


# ── The numbers, not the labels (cold round on #485) ──────────────────
#
# The tests above assert that `report()` prints the right headings and the right
# prose. That left every printed VALUE unasserted: the median, p90 and fh/s
# columns, both lock-on percentages, both ceiling percentages and the delta line
# could all be computed wrong and the suite stayed green. Eight production
# mutations survived it, including `np.hypot(dx, dy)` -> `abs(dx)` — a speed
# that ignores vertical motion, in a sport played vertically.


def diagonal_track(*steps, dt: float = 0.1):
    """A track moving in BOTH axes. `steps` are (dx, dy) pixel deltas.

    `track()` above pins y=0.0, so every speed it produces is horizontal. A
    tool for measuring ball speed in volleyball cannot be tested only sideways.
    """
    x = y = 0.0
    positions = [B.BallPosition(frame=0, time=0.0, x=0.0, y=0.0, confidence=1.0)]
    for i, (dx, dy) in enumerate(steps, start=1):
        x += dx
        y += dy
        positions.append(
            B.BallPosition(frame=i, time=i * dt, x=x, y=y, confidence=1.0)
        )
    return B.TrackedBall(positions=positions)


def row_percent(text: str, label_fragment: str) -> float:
    """The percentage on the row whose label contains `label_fragment`.

    Parsed, not substring-matched. `"0.0%" in row` is true of "100.0%", so a
    row asserted that way passes at both 0% and 100% — which is how the
    px-per-sample test below first failed to catch its own bug being restored.
    """
    row = next(ln for ln in text.splitlines() if label_fragment in ln)
    return float(row.split("%")[0].split()[-1])


def test_speed_is_the_euclidean_distance_not_the_horizontal_one():
    """A 3-4-5 triangle: 30px across and 40px down in 0.1s is 500 px/s, not 300."""
    samples = consecutive_speeds(diagonal_track((30.0, 40.0)))
    assert len(samples) == 1
    assert samples[0].pxps == pytest.approx(500.0)


def test_a_purely_vertical_move_has_a_speed():
    """`abs(dx)` scores a spike straight down as a stationary ball."""
    samples = consecutive_speeds(diagonal_track((0.0, 50.0)))
    assert samples[0].pxps == pytest.approx(500.0)


def test_the_reported_median_and_p90_are_the_median_and_the_p90():
    # Deliberately SKEWED: an evenly spaced run has mean == median, so
    # `np.median` -> `np.mean` would survive it. Nine samples, eight of them
    # 100 and one 9100: median 100, mean 1100, p90 ~3700.
    text = report(track(*([100.0] * 8 + [9100.0])), frame_height=1000)

    def value(label):
        row = next(ln for ln in text.splitlines() if ln.strip().startswith(label))
        return float(row.split("px/s")[0].split()[-1])

    # Asserted on the parsed number rather than a whitespace-exact literal, so
    # a column-width tweak is not a test failure.
    # Pinned to the exact values. An inequality here ("p90 is not the mean")
    # passes against p90 printing the MEDIAN, which is the likelier slip.
    assert value("median") == pytest.approx(100.0), text
    assert value("p90") == pytest.approx(1900.0), text


def test_the_frame_height_column_divides_by_the_frame_height():
    """Inverted (multiply) or dropped, the fh/s column is still printed and
    still looks like a plausible small number."""
    text = report(track(500.0, 500.0, 500.0), frame_height=1000)
    # 500 px/s at 1000px tall is 0.5 fh/s.
    assert "0.500 fh/s" in text, text


def test_the_lock_on_row_counts_samples_under_the_stationary_threshold():
    """Two of four under 10 px/s -> 50%, the number CF-229 asks about.

    The speeds 30 and 40 sit BETWEEN STATIONARY_PXPS (10) and the held-ball
    floor SEG_MIN_MEDIAN_SPEED_PXPS (60), so the two rows must disagree: 50%
    and 100%. A fixture whose speeds are under both thresholds lets the
    lock-on row silently use the wrong constant.
    """
    assert STATIONARY_PXPS < 30.0 < 40.0 < B.SEG_MIN_MEDIAN_SPEED_PXPS, (
        "fixture no longer straddles the two thresholds"
    )
    text = report(track(0.0, 1.0, 30.0, 40.0), frame_height=1000)
    assert row_percent(text, f"under {STATIONARY_PXPS:.0f} px/s") == pytest.approx(50.0), text
    assert row_percent(
        text, f"under {B.SEG_MIN_MEDIAN_SPEED_PXPS:.0f} px/s"
    ) == pytest.approx(100.0), text


def test_the_px_per_sample_row_uses_the_samples_own_step_not_the_permitted_gap():
    """The cold round's finding: `MAX_SAMPLE_GAP_SEC` is the maximum PERMITTED
    gap (1.0s), not the step (0.1s at the shipped cadence). Using it inflated
    this row 10x.

    At dt=0.1 a sample must exceed MAX_JUMP_PX/0.1 = 3000 px/s to have moved
    MAX_JUMP_PX=300 pixels. 2999 has not; 3001 has.
    """
    text = report(track(2999.0, 2999.0, 2999.0, 2999.0), frame_height=1000)
    assert f"over {B.MAX_JUMP_PX:.0f} px in one sample" in text
    assert row_percent(text, "px in one sample") == pytest.approx(0.0), text

    text = report(track(3001.0, 3001.0, 3001.0, 3001.0), frame_height=1000)
    assert row_percent(text, "px in one sample") == pytest.approx(100.0), text


def test_an_empty_sample_set_is_no_fraction_rather_than_all_of_it():
    assert fraction([], lambda s: True) == 0.0


def test_a_gap_at_exactly_the_limit_is_kept():
    """`dt > max_gap` drops it, `dt >= max_gap` would drop the boundary too."""
    kept = consecutive_speeds(track(100.0, dt=B.MAX_SAMPLE_GAP_SEC))
    assert len(kept) == 1
    dropped = consecutive_speeds(track(100.0, dt=B.MAX_SAMPLE_GAP_SEC * 1.01))
    assert dropped == []
