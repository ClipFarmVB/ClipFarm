"""
Frame-height scaling of the contact-detection thresholds (CF-174).

ml/pipeline/ball.py imports OpenCV lazily, so find_contacts and the
segmentation below it are importable here with numpy alone — matching what the
ml/tests CI job installs.

Unlike the rest of ml/tests this file cannot stub numpy: it exercises the real
trajectory maths. The importorskip keeps a missing numpy from aborting
*collection* and taking the other ~100 stdlib-only tests down with it. That skip
cannot hide in CI — the job runs `pip install ... numpy` as its own step, so an
unavailable numpy fails the build before pytest starts.
"""
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.pipeline.ball as ball  # noqa: E402
from ml.pipeline.ball import (  # noqa: E402
    MAX_VALIDATED_FRAME_HEIGHT,
    REFERENCE_FRAME_HEIGHT,
    SEG_MAX_SPEED_PXPS,
    SEG_MIN_MEDIAN_SPEED_PXPS,
    BallPosition,
    TrackedBall,
    _scale_for,
    _segment_track,
    classify_contact_action,
    find_contacts,
)

RESOLUTIONS = (360, 720, 1080)


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


def rally_track(frame_height: int = 360, speed_fhps: float = 0.9, **kw) -> TrackedBall:
    """
    The same *physical* trajectory rendered at a given resolution: speed is a
    fraction of frame height, so 1080p is a 3x-scaled copy of the 360p one.

    The default 0.9 frame-heights/s is a brisk but ordinary ball, and it is
    inside the band that survives at every resolution tested. That band is
    narrow and it narrows with resolution — see TestInvarianceBreaksDownAbove1080p,
    which pins where it ends rather than letting this default imply it has no end.
    """
    return track_at_speed(speed_fhps * frame_height, frame_height=frame_height, **kw)


def physical_track(frame_height: int, before_fhps: float, after_fhps: float,
                   y_frac: float = 0.35, step: float = 0.1, n: int = 9):
    """
    One physical trajectory rendered at `frame_height`: a ball moving down-right
    at before_fhps, redirected downward at after_fhps. Speeds are frame-heights
    per second, so every resolution here is a scaled copy of the same motion.
    """
    positions, x, y = [], 0.1 * frame_height, y_frac * frame_height
    for i in range(n):
        positions.append(BallPosition(
            frame=i, time=i * step, x=x, y=y, confidence=0.8,
        ))
        v = (before_fhps if i < 4 else after_fhps) * frame_height
        x += v * step * 0.6
        y += v * step * 0.8
    return positions


class TestScaleFor:
    def test_reference_height_is_identity(self):
        assert _scale_for(int(REFERENCE_FRAME_HEIGHT)) == 1.0

    def test_scales_linearly_with_height(self):
        assert _scale_for(1080) == pytest.approx(3.0)
        assert _scale_for(720) == pytest.approx(2.0)

    def test_unknown_height_falls_back_to_reference_and_warns(self, caplog):
        """The warning has to name the space it assumed — that is the actionable
        part. It must not name a caller: tune_contacts reaches _scale_for too."""
        import logging
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            assert _scale_for(0) == 1.0
        assert f"{REFERENCE_FRAME_HEIGHT:.0f}px" in caplog.text
        assert "find_contacts" not in caplog.text


class TestResolutionInvariance:
    """
    Invariance holds inside the band between the scaled contact floor and the
    *unscaled* segmentation ceiling. These tests cover that band; the class
    below pins where it stops.
    """

    def test_same_trajectory_same_contacts_at_any_resolution(self):
        """
        The point of CF-174: identical physical motion filmed at 360p and 1080p
        must yield the same contacts.
        """
        contacts = {
            h: find_contacts(rally_track(h), frame_height=h) for h in RESOLUTIONS
        }
        counts = {h: len(c) for h, c in contacts.items()}
        assert set(counts.values()) == {1}, f"contact count varies by resolution: {counts}"

        times = {h: [round(c["time"], 3) for c in lst] for h, lst in contacts.items()}
        assert times[360] == times[720] == times[1080]

    @pytest.mark.parametrize("speed_fhps", [0.7, 0.8, 0.9, 1.0])
    def test_invariance_across_the_surviving_band(self, speed_fhps):
        """
        Not just the one speed the default picks: every physical speed between
        the floor (0.667 fh/s) and the 1080p ceiling (1.111 fh/s) must agree.
        """
        counts = {
            h: len(find_contacts(rally_track(h, speed_fhps), frame_height=h))
            for h in RESOLUTIONS
        }
        assert len(set(counts.values())) == 1, (
            f"{speed_fhps} fh/s is inside the band but varies by resolution: {counts}"
        )

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


class TestInvarianceBreaksDownAbove1080p:
    """
    CF-174 normalizes the contact *floor* and cannot normalize the segmentation
    *ceiling*, so the admissible band closes as resolution rises:

        360p  0.667-3.333 fh/s (5.00x)   1080p  0.667-1.111 (1.67x)
        720p  0.667-1.667     (2.50x)    1440p  0.667-0.833 (1.25x)

    Past 1440p — the clamp point — the scale would freeze and the band would
    slide below real play, so _scale_for reverts to unscaled instead. 2160p
    therefore behaves as `main` does: 0.111-0.556 fh/s, wrong but not narrower.

    These tests pin all of that, so nobody reads the class above as "resolution
    independent" full stop. Deleting them requires scaling SEG_MAX_SPEED_PXPS,
    which is the actual fix and is blocked on the tracking pollution documented
    at that constant.
    """

    @pytest.mark.parametrize("speed_fhps", [1.2, 2.0, 3.0])
    def test_fast_physical_motion_survives_at_360p_and_not_at_1080p(self, speed_fhps):
        """
        Above 1.111 fh/s the 1080p copy exceeds the fixed 1200 px/s teleport
        split and loses its contact while the identical 360p motion keeps it. A
        volleyball spike lives here, which is why the highlight consumer
        (contacts_to_rallies, MIN_RALLY_CONTACTS=3) is the exposed one.
        """
        assert len(find_contacts(rally_track(360, speed_fhps), frame_height=360)) == 1
        assert find_contacts(rally_track(1080, speed_fhps), frame_height=1080) == []

    def test_4k_keeps_the_band_main_has_rather_than_a_narrower_one(self):
        """
        The regression guard. A clamped scale at 2160p admits only
        0.444-0.556 fh/s and finds nothing across the plausible range; unscaled
        thresholds admit 0.111-0.556 and do find contacts there. Reverting past
        the clamp point is what keeps this PR from being worse than `main` at 4K.
        """
        for speed_fhps in (0.2, 0.3, 0.4, 0.5):
            track = rally_track(2160, speed_fhps)
            assert len(find_contacts(track, frame_height=2160)) == 1, (
                f"{speed_fhps} fh/s is inside the band `main` has at 2160p and must "
                "still be found — losing it is the regression this reverts"
            )

    def test_4k_is_still_wrong_just_not_narrower(self):
        """
        Reverting is an interim, not a fix. Ordinary play at 0.8 fh/s is above
        the unscaled ceiling at 2160p and is still lost — exactly as it is on
        `main` today. CF-229 is what actually fixes this.
        """
        for speed_fhps in (0.8, 1.5, 3.0):
            assert find_contacts(rally_track(2160, speed_fhps), frame_height=2160) == []

    def test_the_validated_ceiling_is_declared(self):
        """The fixtures CF-174 was measured on stop at 1080p; the constant says so."""
        assert MAX_VALIDATED_FRAME_HEIGHT == 1080

    def test_footage_above_the_validated_ceiling_is_logged_loudly(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR, logger="ml.pipeline.ball"):
            _scale_for(2160)
        assert any(r.levelno >= logging.ERROR for r in caplog.records), (
            "an unvalidated resolution must not pass at warning level or below"
        )
        assert "frame-heights/s" in caplog.text, "say how narrow the band actually is"

    def test_severity_splits_at_the_clamp_point_not_the_validated_ceiling(self, caplog):
        """
        The band closes continuously — 1080p leaves 1.67x and 1081p leaves 1.666x,
        the same footage in every practical sense — so error level one pixel above
        MAX_VALIDATED_FRAME_HEIGHT would page someone about a video that behaves
        like the validated case. Error is for clamped footage, where the band has
        bottomed out at 1.25x; in between, the printed numbers are the warning.
        """
        import logging

        clamp_height = int(
            ball.CONTACT_SPEED_CEILING_FRAC * SEG_MAX_SPEED_PXPS
            / ball.CONTACT_HIT_SPEED_PXPS * REFERENCE_FRAME_HEIGHT
        )
        assert clamp_height > MAX_VALIDATED_FRAME_HEIGHT, (
            "with no gap between the two there is nothing to warn about, and this "
            "test would pass by never exercising the warning branch"
        )

        # Unvalidated but unclamped: must still speak up, must not page.
        for height in (MAX_VALIDATED_FRAME_HEIGHT + 1, clamp_height):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
                _scale_for(height)
            assert caplog.records, f"{height}px is unmeasured — it must say so"
            assert all(r.levelno < logging.ERROR for r in caplog.records), (
                f"{height}px is not clamped, so its band is no worse than 1440p's "
                f"1.25x — error level overstates it"
            )

        # One pixel past the clamp, the scale stops tracking resolution at all.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            _scale_for(clamp_height + 1)
        assert any(r.levelno >= logging.ERROR for r in caplog.records), (
            "clamped footage is the case that genuinely finds nothing"
        )

    def test_an_empty_return_names_the_gates_that_rejected_everything(self, caplog):
        """
        Zero contacts is indistinguishable downstream from "no ball in this
        video". 0.3 fh/s at 1440p — just under the clamp point, so still
        scaled — segments fine and then dies on the scaled floor. The log has
        to say so.
        """
        import logging
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            assert find_contacts(rally_track(1440, 0.3), frame_height=1440) == []
        assert "found nothing" in caplog.text
        assert "hit_speed" in caplog.text

    def test_an_empty_return_names_the_segment_filter_when_that_is_the_cause(self, caplog):
        """
        The other way to reach zero: 1.5 fh/s at 2160p is above the fixed
        teleport ceiling, so every pair splits and no segment survives. Distinct
        cause, distinct message — condense cuts the whole video either way.
        """
        import logging
        with caplog.at_level(logging.WARNING, logger="ml.pipeline.ball"):
            assert find_contacts(rally_track(2160, 1.5), frame_height=2160) == []
        assert "kept no segments" in caplog.text


CLAMP_HEIGHT = (
    ball.CONTACT_SPEED_CEILING_FRAC * SEG_MAX_SPEED_PXPS
    / ball.CONTACT_HIT_SPEED_PXPS * REFERENCE_FRAME_HEIGHT
)


class TestPastTheClampPointItRevertsRatherThanExtrapolating:
    """
    The scaled hit-speed floor walks into the *unscaled* SEG_MAX_SPEED_PXPS
    ceiling, and freezing the scale to keep them apart is not a weaker
    normalization — it is the wrong one. A frozen scale under a growing frame
    slides the whole admissible band below where real play lives, and measurably
    below what `main` finds with no scaling at all.

    So past that height _scale_for returns 1.0. Over-firing, but never narrower
    than today, so no resolution regresses against `main` while CF-229 is open.
    """

    def test_the_collision_is_real_without_a_cap(self):
        """The premise: unclamped, the floor eventually meets the ceiling — at a
        height people actually shoot, which is what makes the cap necessary.

        Derived from the constants rather than hardcoded. CF-103 lowering
        CONTACT_HIT_SPEED_PXPS from 240 to 220 moves this from 1800p to ~1964p;
        a test asserting the old number fails on a change that does not weaken
        the premise at all, and one asserting the new number would rot the same
        way at the next tune.
        """
        collision_fh = (
            REFERENCE_FRAME_HEIGHT * SEG_MAX_SPEED_PXPS / ball.CONTACT_HIT_SPEED_PXPS
        )
        assert collision_fh <= 2160, (
            f"the floor meets the ceiling only at {collision_fh:.0f}p, above the "
            "2160p phones shoot — the cap would be guarding nothing"
        )
        unclamped = collision_fh / REFERENCE_FRAME_HEIGHT
        assert ball.CONTACT_HIT_SPEED_PXPS * unclamped >= SEG_MAX_SPEED_PXPS

    def test_past_the_clamp_point_reverts_to_unscaled(self):
        assert _scale_for(2160) == 1.0
        assert _scale_for(int(CLAMP_HEIGHT) + 1) == 1.0

    def test_the_revert_is_never_narrower_than_main(self):
        """The property that makes this not a regression: at every resolution the
        admissible band is at least as wide as unscaled thresholds would give."""
        for h in (720, 1080, 1440, 2160, 2880):
            floor = ball.CONTACT_HIT_SPEED_PXPS * _scale_for(h)
            assert floor <= ball.CONTACT_HIT_SPEED_PXPS * (h / REFERENCE_FRAME_HEIGHT)
            assert floor < SEG_MAX_SPEED_PXPS, "floor and ceiling must never be disjoint"

    def test_reverting_says_so_at_error_level(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR, logger="ml.pipeline.ball"):
            _scale_for(2160)
        assert any(r.levelno >= logging.ERROR for r in caplog.records)
        assert "UNSCALED" in caplog.text, "the log must name what it fell back to"

    def test_up_to_the_clamp_point_still_scales(self):
        """The floor holds at the validated 0.667 fh/s right up to the boundary;
        only the ceiling closes. Reverting early would hand a 1081p video main's
        behaviour while 1080p gets the good one."""
        for h in (1081, 1200, int(CLAMP_HEIGHT)):
            assert _scale_for(h) == pytest.approx(h / REFERENCE_FRAME_HEIGHT)

    def test_measured_resolutions_are_untouched(self):
        """Every fixture CF-174 was measured on is <= 1080p; their numbers must not move."""
        assert _scale_for(int(REFERENCE_FRAME_HEIGHT)) == 1.0
        assert _scale_for(720) == pytest.approx(2.0)
        assert _scale_for(1080) == pytest.approx(3.0)


class TestActionClassificationIsNormalized:
    """
    classify_contact_action's thresholds are px/s too. Left raw, the same hit
    changed label with resolution — and since find_contacts' scaled gate admits
    nothing under 720 px/s at 1080p, the SET band (45-240) became unreachable
    there by construction. It normalizes velocities into reference space instead.
    """

    @pytest.mark.parametrize(("before_fhps", "after_fhps"), [
        (0.20, 0.50), (0.15, 0.35), (0.10, 0.25), (0.30, 0.90),
    ])
    def test_same_physical_hit_gets_the_same_label(self, before_fhps, after_fhps):
        labels = {
            h: classify_contact_action(physical_track(h, before_fhps, after_fhps), 4, h)[0]
            for h in (360, 720, 1080, 2160)
        }
        assert len(set(labels.values())) == 1, f"label varies by resolution: {labels}"

    def test_confidence_is_resolution_independent_too(self):
        """It is derived from sp_after, so it drifts by the same mechanism."""
        confs = {
            h: classify_contact_action(physical_track(h, 0.30, 0.90), 4, h)[1]
            for h in (360, 720, 1080)
        }
        assert len(set(confs.values())) == 1, f"confidence varies by resolution: {confs}"

    def test_the_set_band_is_still_reachable_at_1080p(self):
        """The branch the raw thresholds had made dead at 1080p."""
        labels = [
            classify_contact_action(physical_track(1080, b, a), 4, 1080)[0]
            for b, a in ((0.10, 0.25), (0.15, 0.35), (0.20, 0.50))
        ]
        assert "set" in labels


class TestSegmentationIsNotScaled:
    """
    SEG_* answers "is this one object?", not "how hard was this hit", and is
    deliberately left unscaled: scaling SEG_MIN_MEDIAN_SPEED_PXPS rejected every
    segment on wide-angle 1080p footage (test4: 96 segments -> 0). Asserted
    behaviourally — a source-text check passes on any equivalent spelling.
    """

    def test_segmentation_cannot_see_the_resolution(self):
        """
        The structural half: _segment_track takes positions and nothing else, so
        scaling its thresholds means threading frame_height in and tripping this.
        """
        import inspect
        assert list(inspect.signature(_segment_track).parameters) == ["positions"]

    @pytest.mark.parametrize("frame_height", [360, 720, 1080, 2160])
    def test_the_teleport_ceiling_stays_at_a_fixed_pixel_speed(self, frame_height):
        """
        1300 px/s is above SEG_MAX_SPEED_PXPS in absolute pixels, so it splits as
        a track hop no matter what the caller claims the frame height is. Scale
        the ceiling and the taller cases start returning a contact here.
        """
        too_fast = track_at_speed(1300.0, frame_height=frame_height)
        assert find_contacts(too_fast, frame_height=frame_height) == []

    def test_the_stationary_filter_stays_at_a_fixed_pixel_speed(self):
        """
        100 px/s is above SEG_MIN_MEDIAN_SPEED_PXPS (60) and below a 1080p-scaled
        version of it (180), so a kept segment here is proof the filter is
        unscaled. This is the one whose scaling collapsed test4 to zero.
        """
        assert SEG_MIN_MEDIAN_SPEED_PXPS < 100.0 < SEG_MIN_MEDIAN_SPEED_PXPS * 3
        drifting = track_at_speed(100.0, frame_height=1080)
        assert len(_segment_track(drifting.positions)) == 1

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
    def test_module_imports_with_cv2_unavailable(self, monkeypatch):
        """
        ml/tests installs no OpenCV; keep the import lazy or CI breaks. A None
        entry in sys.modules makes `import cv2` raise, so this holds whether or
        not OpenCV happens to be installed where the test runs.
        """
        monkeypatch.setitem(sys.modules, "cv2", None)
        sys.modules.pop("ml.pipeline.ball", None)
        try:
            import ml.pipeline.ball as reimported

            assert reimported.find_contacts is not None
        finally:
            # Same discipline as test_ball_runtime_guard: drop the module object
            # rather than leaving one that closed over the blocked cv2, and clear
            # the parent attribute that `from ml.pipeline import ball` reads.
            sys.modules.pop("ml.pipeline.ball", None)
            parent = sys.modules.get("ml.pipeline")
            if isinstance(getattr(parent, "ball", None), types.ModuleType):
                delattr(parent, "ball")

    def test_the_video_decoding_entry_points_still_need_it(self, monkeypatch):
        """
        Lazy, not removed. With cv2 unimportable, track_ball must fail on that
        import rather than getting further — which is what proves the import is
        real and reached, without asserting on source text.
        """
        monkeypatch.setitem(sys.modules, "cv2", None)
        with pytest.raises(ImportError):
            ball.track_ball("nonexistent.mp4", api_key="k")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
