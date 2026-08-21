"""
Frame-height scaling of the contact-detection thresholds (CF-174).

ml/pipeline/ball.py imports OpenCV lazily, so find_contacts and the
segmentation below it are importable here with numpy alone — matching what the
ml/tests CI job installs.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.pipeline.ball as ball  # noqa: E402
from ml.pipeline.ball import (  # noqa: E402
    REFERENCE_FRAME_HEIGHT,
    SEG_MAX_SPEED_PXPS,
    BallPosition,
    TrackedBall,
    _scale_for,
    find_contacts,
)


def track_at_speed(
    speed_pxps: float, frame_height: int = 360, step: float = 0.33,
    hit_at: int = 8, n: int = 16,
) -> TrackedBall:
    """A ball flying right at speed_pxps, struck at `hit_at` so it reverses."""
    positions, x, y = [], 0.2 * frame_height, 0.5 * frame_height
    for i in range(n):
        positions.append(BallPosition(
            frame=int(i * step * 30), time=i * step, x=x, y=y, confidence=0.8,
        ))
        x += (1.0 if i < hit_at else -1.0) * speed_pxps * step
    return TrackedBall(positions=positions)


def rally_track(frame_height: int = 360, **kw) -> TrackedBall:
    """
    The same *physical* trajectory rendered at a given resolution: speed is a
    fraction of frame height, so 1080p is a 3x-scaled copy of the 360p one.

    0.9 frame-heights/s is a brisk but ordinary ball, chosen to stay under the
    (unscaled) SEG_MAX_SPEED_PXPS teleport split at every resolution tested —
    see TestSegmentationIsNotScaled for why that ceiling exists.
    """
    return track_at_speed(0.9 * frame_height, frame_height=frame_height, **kw)


class TestScaleFor:
    def test_reference_height_is_identity(self):
        assert _scale_for(int(REFERENCE_FRAME_HEIGHT)) == 1.0

    def test_scales_linearly_with_height(self):
        assert _scale_for(1080) == pytest.approx(3.0)
        assert _scale_for(720) == pytest.approx(2.0)

    def test_unknown_height_falls_back_to_reference_and_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            assert _scale_for(0) == 1.0
        assert "frame_height" in caplog.text


class TestResolutionInvariance:
    def test_same_trajectory_same_contacts_at_any_resolution(self):
        """
        The point of CF-174: identical physical motion filmed at 360p and 1080p
        must yield the same contacts.
        """
        contacts = {
            h: find_contacts(rally_track(h), frame_height=h) for h in (360, 720, 1080)
        }
        counts = {h: len(c) for h, c in contacts.items()}
        assert set(counts.values()) == {1}, f"contact count varies by resolution: {counts}"

        times = {h: [round(c["time"], 3) for c in lst] for h, lst in contacts.items()}
        assert times[360] == times[720] == times[1080]

    def test_unscaled_thresholds_overfire_at_1080p(self):
        """
        Guards the premise, not just the fix. A ball drifting at 300 px/s is
        slow *for 1080p* (0.28 frame-heights/s) and should be ignored, which is
        what the scaled thresholds do. Judged by the raw 360p constants — what
        shipped before CF-174 — the same motion clears every gate and emits a
        contact. That gap is the resolution bug.
        """
        drifting = track_at_speed(300.0, frame_height=1080)
        assert find_contacts(drifting, frame_height=1080) == []
        assert len(find_contacts(drifting, frame_height=0)) == 1


class TestContactCeilingClamp:
    """
    The scaled hit-speed floor walks into the *unscaled* SEG_MAX_SPEED_PXPS
    ceiling. _segment_track guarantees speed stays below that ceiling inside a
    segment, so once the floor reaches it no sample can satisfy both gates and
    find_contacts returns nothing — no keep-windows, no clips, no error.
    """

    def test_the_collision_is_real_without_a_cap(self):
        """The premise: unclamped, the floor meets the ceiling at 1800p."""
        unclamped = 1800 / REFERENCE_FRAME_HEIGHT
        assert ball.CONTACT_HIT_SPEED_PXPS * unclamped >= SEG_MAX_SPEED_PXPS

    def test_scale_is_capped_and_warns(self, caplog):
        import logging
        cap = (
            ball.CONTACT_SPEED_CEILING_FRAC * SEG_MAX_SPEED_PXPS
            / ball.CONTACT_HIT_SPEED_PXPS
        )
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            assert _scale_for(2160) == pytest.approx(cap)
        assert "capped" in caplog.text

    def test_capped_floor_stays_under_the_segmentation_ceiling(self):
        assert ball.CONTACT_HIT_SPEED_PXPS * _scale_for(2160) < SEG_MAX_SPEED_PXPS

    def test_4k_footage_still_produces_contacts(self):
        """
        The regression this guards. A ball at 1100 px/s sits inside the band the
        clamp preserves — under the 1200 px/s teleport split, over the capped
        960 px/s floor. Unclamped, the floor would be 1440 px/s and this returns
        zero contacts at every speed the segmenter admits.
        """
        struck = track_at_speed(1100.0, frame_height=2160)
        assert len(find_contacts(struck, frame_height=2160)) == 1

    def test_measured_resolutions_are_untouched_by_the_cap(self):
        """Every fixture CF-174 was measured on is <= 1080p; their numbers must not move."""
        assert _scale_for(int(REFERENCE_FRAME_HEIGHT)) == 1.0
        assert _scale_for(720) == pytest.approx(2.0)
        assert _scale_for(1080) == pytest.approx(3.0)


class TestSegmentationIsNotScaled:
    def test_seg_thresholds_ignore_frame_height(self):
        """
        SEG_* answers "is this one object?", not "how hard was this hit", and is
        deliberately left unscaled: scaling SEG_MIN_MEDIAN_SPEED_PXPS rejected
        every segment on wide-angle 1080p footage (test4: 96 segments -> 0).
        """
        seg_block = Path(ball.__file__).read_text().split("# ── Contact detection config")[0]
        assert "SEG_MAX_SPEED_PXPS * scale" not in seg_block
        assert "SEG_MIN_MEDIAN_SPEED_PXPS * scale" not in seg_block

    def test_known_cost_fast_1080p_motion_splits_as_a_teleport(self):
        """
        Documents the accepted trade-off rather than asserting it is good: with
        SEG_MAX_SPEED_PXPS unscaled, motion above it at 1080p (12.2% of samples
        on test4, 4.4% on test2, 0% on 360p test1) is split as a track hop and
        loses its contacts. Scaling it is the obvious fix and it is NOT applied
        here, because doing so merges test4's stationary false-positive track
        into the ball's segments and collapses that fixture to zero contacts.
        The real blocker is that tracking pollution — see the CF-174 follow-up.
        """
        too_fast = track_at_speed(SEG_MAX_SPEED_PXPS * 1.2, frame_height=1080)
        assert find_contacts(too_fast, frame_height=1080) == []


class TestLazyOpenCV:
    def test_module_imports_without_cv2(self):
        """ml/tests installs no OpenCV; keep the import lazy or CI breaks."""
        header = Path(ball.__file__).read_text().split("logger =")[0]
        assert "import cv2" not in header


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
