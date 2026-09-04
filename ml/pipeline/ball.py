"""
Ball detection, tracking, and contact detection pipeline.

Takes a video file and returns a list of contact timestamps — moments
where the ball trajectory changed sharply, indicating a player hit.

Pipeline:
  1. Run Roboflow ball detector on sampled frames  (detect)
  2. Link per-frame detections into a trajectory   (track)
  3. Find sharp velocity changes in the trajectory (contacts)

Usage:
  from ml.pipeline.ball import detect_contacts
  contacts = detect_contacts("game.mp4", api_key="...")
  # -> [{"time": 12.3, "x": 540, "y": 210}, ...]
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# cv2 is imported lazily, inside each function that actually opens a video:
# track_ball, _read_frame_height and detect_rallies. (detect_contacts needs no
# import of its own — it reaches cv2 only through the two it calls.) Everything
# else here — segmentation, find_contacts, contacts_to_rallies — is pure
# trajectory maths, and keeping OpenCV out of module import lets the eval
# tooling and unit tests exercise it with numpy alone (ml/tests installs no
# OpenCV).

logger = logging.getLogger(__name__)

# ── Detection config ──────────────────────────────────────────────────────────
MODEL_ID     = "volleyball-ball-tracking-0eo7r/3"
SAMPLE_EVERY = 3          # run inference every Nth frame (10 fps from 30 fps source)
MIN_CONF     = 0.40       # minimum detection confidence to consider

# ── Tracking config ───────────────────────────────────────────────────────────
MAX_JUMP_PX  = 300        # max pixels a ball can move between sampled frames
                          # detections further than this from the predicted
                          # position are treated as a different object
MAX_MISS     = 5          # max consecutive missed frames before track is reset

# ── Track segmentation config ─────────────────────────────────────────────────
# The raw track is a chimera: _pick_active hops between the game ball, spare
# balls, and false detections (measured: 23% of consecutive positions jump
# >200px). Contacts must only be detected within coherent single-ball
# segments, so the track is split wherever motion is implausible for one ball.
#
# ALL speeds are px/SECOND so thresholds hold at any source frame rate
# (tuned on 30fps footage; a 60fps video halves px/frame velocities but
# leaves px/second untouched).
#
# These two are deliberately NOT frame-height-scaled, unlike the contact
# thresholds below (CF-174) — and that is a known compromise, not a clean
# result. They answer "is this one physical object?" rather than "how hard was
# this hit".
#
# SEG_MIN_MEDIAN_SPEED_PXPS sits in a wide valley between detector jitter
# (~0-5 px/s at any resolution) and real ball motion, so the reference value
# separates the two modes everywhere measured. Scaling it moves the cutoff
# *into* the real-motion distribution on wide-angle footage: on test4 (1080p,
# distant camera, ball median ~0.1 frame-heights/s vs ~0.3 on test1/test2) a 3x
# threshold rejected every segment — 96 segments/965 positions became 0 and the
# condense stage cut the whole video. Camera framing varies independently of
# resolution, and frame height cannot normalize it.
#
# SEG_MAX_SPEED_PXPS *should* scale by the same argument as the contact
# thresholds, and leaving it fixed costs real contacts: at 1080p it splits
# 12.2% of test4's samples and 4.4% of test2's as bogus track hops (0% on 360p
# test1). It is left unscaled anyway because raising it merges test4's
# stationary false-positive detections into the ball's own segments, whose
# median speed then falls under the filter above — same collapse to zero. The
# blocker is that tracking pollution, not this constant; fix the tracker first
# (see the CF-174 follow-up), then scale this.
SEG_MAX_SPEED_PXPS        = 1200.0  # px/s: faster displacement = track hop, split here
SEG_MIN_POSITIONS         = 4       # segments shorter than this are junk (hops/flicker)
SEG_MIN_MEDIAN_SPEED_PXPS = 60.0    # px/s: near-stationary segments are held/spare balls

# ── Contact detection config ──────────────────────────────────────────────────
# A contact is a deviation from ballistic (free-fall) flight. Raw angle/speed
# thresholds misfire badly at coarse sampling: gravity alone bends the
# trajectory >25° between samples 0.33s apart, flagging normal flight as hits.
# Gravity is estimated per-video from coherent segments (median vertical
# acceleration — free flight dominates, so the median is robust to hits).
# The residual floor was originally 480 px/s, from a p75 noise estimate read off
# a single cached trajectory by inspection. Scored against hand-labeled rallies
# (CF-98 harness, #116) that proved ~2x too high: it rejected every contact in
# 60 of 126 labeled rallies, and since keep-windows are anchored on contacts,
# the condense stage then cut those rallies as dead time.
#
# 240 is a recall-vs-condense tradeoff, not a strict optimum. On test1, 180
# scores marginally better on both recall numbers (102 vs 101 of 126 rallies
# hit, 167s vs 176s of live play lost) but removes less dead time; 240 was
# picked because below it the recall gains are marginal while the condensed
# video keeps growing. Further down the contact count still climbs with
# rallies-hit flat, i.e. only false positives are being added.
#
# Measured on the dead-time metric only. find_contacts feeds a second consumer:
# tasks.py runs it through contacts_to_rallies for highlight clips, where
# MIN_RALLY_CONTACTS gates at 3 — so the extra contacts can lift marginal 1-2
# contact segments over that line and emit junk clips. That side is unmeasured;
# score it with the CF-55 highlight mode against results/test1.jsonl before
# tuning this further.
#
# Re-tune by scoring, not by inspecting a trajectory:
#   docker compose --env-file .env.docker run --rm --no-deps eval python -m ml.eval.tune_contacts
#
# CF-174: the two px/s constants below are scaled by
# frame_height / REFERENCE_FRAME_HEIGHT at use (see _scale_for). px/s is frame-rate
# independent but not frame-*size* independent — the same physical motion covers
# ~3x more pixels at 1080p, so a floor meaning "a decisive hit" at 360p means
# "a gentle lob, or jitter" at 1080p. Measured on the shipping condense path:
# unscaled, 1080p test2 removed only 9.5% of dead time (condensed video 93% as
# long as the source). At 360p the factor is exactly 1.0, so everything tuned
# above is preserved bit-for-bit. CONTACT_RESIDUAL_RATIO needs no scaling — it
# multiplies a speed already in this video's pixel space.
#
# There was a third, MIN_SPEED_PXPS = 120: "both sides near-stationary, skip".
# It was dead code. It carried the same scale as CONTACT_HIT_SPEED_PXPS at half
# the value, so every sample it rejected was rejected again by the hit-speed
# gate on the next line — mutation-checked, setting it to 0.0 moved no test and
# no fixture number. Scaling it measured nothing, and leaving it would have let
# a later tuning pass drop CONTACT_HIT_SPEED_PXPS underneath it and silently
# switch on a gate that has never fired in production. Removed rather than
# documented; near-stationary balls are rejected by hit_speed alone.
REFERENCE_FRAME_HEIGHT    = 360.0   # tracking space the px/s constants below assume
CONTACT_RESIDUAL_RATIO    = 0.50    # residual must exceed this fraction of ball speed
CONTACT_RESIDUAL_MIN_PXPS = 240.0   # ...and this absolute floor (px/s, above noise)
CONTACT_HIT_SPEED_PXPS    = 240.0   # px/s: a real hit has speed on at least one side
MIN_CONTACT_SPACING       = 0.6     # seconds: debounce — one hit can't fire twice
MAX_SAMPLE_GAP_SEC        = 1.0     # skip triples spanning a detection gap
# The scale factor is capped so the scaled hit-speed floor cannot reach
# SEG_MAX_SPEED_PXPS. _segment_track splits any pair faster than that ceiling,
# so speed inside a segment is always below it — once CONTACT_HIT_SPEED_PXPS *
# scale meets it the two constraints are disjoint and find_contacts returns
# nothing at all. Unclamped that lands at 1800p, and phones shoot 2160p.
#
# Read the cap as damage control, NOT as 4K support. It moves the floor and
# cannot move the ceiling, so the admissible band keeps closing with resolution:
#
#     360p   floor 0.667  ceiling 3.333 frame-heights/s   band 5.00x
#     720p   floor 0.667  ceiling 1.667                   band 2.50x
#    1080p   floor 0.667  ceiling 1.111                   band 1.67x
#    1440p   floor 0.667  ceiling 0.833                   band 1.25x  <- clamp
#
# Every row above ships. What the cap would give past the clamp point does not,
# and is listed only because it is the reason for the row under it:
#
#    2160p   floor 0.444  ceiling 0.556  (frozen scale)   band 1.25x  rejected
#    2160p   floor 0.111  ceiling 0.556  (unscaled)       band 5.00x  shipped
#
# Read the rejected row carefully: the floor drops to 0.444. Below the clamp the
# scale tracks resolution and the floor stays put at the validated 0.667 while
# only the ceiling closes — degradation, but in the right direction. Past the
# clamp the scale is frozen under a growing frame, so the whole band slides
# below where real play lives (0.3-0.9 fh/s). Measured on real tracks: at 2160p
# `main`'s unscaled thresholds still find contacts and this PR's clamped ones
# find none, so past the clamp point normalizing is worse than not.
#
# _scale_for therefore returns 1.0 above the clamp point rather than the capped
# value — the shipped row, i.e. pre-CF-174 behaviour, which over-fires but is
# never narrower, so *above the clamp point* these gates do not regress against
# `main`. Read "never narrower" as scoped to that range and to contact detection,
# because it is false in both other directions. Below the clamp the gates are
# deliberately narrower than `main` — the floor is 240 px/s unscaled and 480/720/
# 960 at 720p/1080p/1440p — which is the entire change, not a regression; the
# warning branch in _scale_for says the same thing about the range between the
# validated ceiling and the clamp. And it does not reach the condense
# path: dead_time.bridge_windows_by_motion scales unconditionally, so above the
# clamp the pipeline joins `main`'s contact set with a 6x-tighter bridge. See the
# asymmetry note in that function. That is an interim, not a fix: the real one is
# scaling SEG_MAX_SPEED_PXPS, blocked on the tracking pollution documented above
# (CF-229), after which the clamp is unnecessary entirely.
CONTACT_SPEED_CEILING_FRAC = 0.80   # cap scaled contact speeds at this × SEG_MAX_SPEED_PXPS
MAX_VALIDATED_FRAME_HEIGHT = 1080   # tallest footage CF-174 was measured on
# Legacy thresholds — still used by fuse_with_ball_contacts "strong contact" check
CONTACT_ANGLE_DEG   = 25.0  # minimum direction change (degrees)
CONTACT_SPEED_RATIO = 0.35  # speed change fraction

# ── Rally clipping config ─────────────────────────────────────────────────────
RALLY_GAP_SECONDS    = 5.0   # gap between contacts that splits two rallies
PRE_RALLY_PAD        = 2.0   # seconds before first contact (capture approach)
POST_PLAY_PAD        = 2.5   # seconds after ball leaves play (celebration, flight)
FLOOR_BOUNCE_ANGLE   = 130.0 # direction change >= this = ball hit the floor (unused for splitting, kept for scoring)
FLOOR_BOUNCE_Y_FRAC  = 0.55  # ball must also be in lower N% of frame
MIN_RALLY_DURATION   = 2.0   # skip clips shorter than this (noise/false contacts)
MIN_RALLY_CONTACTS   = 3     # a rally needs a serve + return at minimum; 1-2 = noise
MAX_CLIP_DURATION    = 30.0  # split long groups into sub-clips of at most this length


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BallPosition:
    frame: int
    time: float
    x: float
    y: float
    confidence: float


@dataclass
class TrackedBall:
    """Running state of the tracked active ball."""
    positions: list[BallPosition] = field(default_factory=list)
    misses: int = 0

    @property
    def last(self) -> Optional[BallPosition]:
        return self.positions[-1] if self.positions else None

    @property
    def velocity(self) -> Optional[tuple[float, float]]:
        """Velocity vector (vx, vy) in px/frame from the last two positions."""
        if len(self.positions) < 2:
            return None
        a, b = self.positions[-2], self.positions[-1]
        dt = b.frame - a.frame
        if dt == 0:
            return None
        return (b.x - a.x) / dt, (b.y - a.y) / dt

    def predict_next(self, at_frame: int) -> Optional[tuple[float, float]]:
        """Extrapolate ball position at `at_frame` using current velocity."""
        if self.last is None:
            return None
        v = self.velocity
        if v is None:
            return self.last.x, self.last.y
        dt = at_frame - self.last.frame
        return self.last.x + v[0] * dt, self.last.y + v[1] * dt


# ─────────────────────────────────────────────────────────────────────────────
# 1. Detection
# ─────────────────────────────────────────────────────────────────────────────

class BallRuntimeUnavailable(RuntimeError):
    """Roboflow `inference` is not importable in THIS process (CF-225).

    Named after `detect.PoseRuntimeUnavailable`, but claiming less than it does:
    that type is translated to `PermanentPipelineError` at the worker boundary
    so Celery stops retrying, and this one is not. Ball tracking has somewhere
    left to go when it fails — the pose-first scan — so a caller that swallowed
    the retry here would be deciding the whole run's fate on one stage.

    What the distinct type is for is telling "no runtime here" apart from
    "tracking ran and failed", which are different problems with different
    fixes. A RuntimeError subclass, so existing broad handling still catches it.

    Which process this is matters: the Modal image (`ml/modal_app.py`) installs
    `inference` and is where tracking is *meant* to run, so this firing there is
    a broken image. Everywhere else it is the expected state — the worker ships
    no ML runtime since CF-164, and a dev checkout does not get one from
    `ml/requirements.txt` either: that file pins `inference-sdk`, a different
    distribution providing `inference_sdk`, not the `inference` this needs.
    So the local-CPU path is effectively Modal-image-only in practice.
    """


def _load_model(api_key: str):
    """Load Roboflow ball detection model (weights cached after first run)."""
    try:
        from inference import get_model
    except ImportError as import_err:
        # Deliberately says only what is true from inside any process: this one
        # cannot run the model. `_load_model` is the primary path on the Modal
        # GPU worker and the fallback path in the Celery worker, so the caller
        # — not this message — is what knows whether that is a broken image or
        # a deployment that never intended to run it here.
        raise BallRuntimeUnavailable(
            f"Roboflow `inference` is not importable in this process ({import_err}), so "
            f"ball model {MODEL_ID} cannot run here. The only place it is installed is "
            "the `clipfarm-ball-tracking` Modal image (ml/modal_app.py pins "
            "inference==1.3.3) — note that ml/requirements.txt carries `inference-sdk`, "
            "a different distribution (module `inference_sdk`, an HTTP client) that does "
            "NOT provide this import."
        ) from import_err
    logger.info("Loading ball detection model %s", MODEL_ID)
    return get_model(MODEL_ID, api_key=api_key)


def _detect_frame(model, frame: np.ndarray) -> list[dict]:
    """
    Run inference on a single frame.
    Returns list of {x, y, confidence} dicts, sorted by confidence desc.
    """
    results = model.infer(frame, confidence=MIN_CONF)
    preds = []
    if results and hasattr(results[0], "predictions"):
        for p in results[0].predictions:
            preds.append({"x": float(p.x), "y": float(p.y), "confidence": float(p.confidence)})
    preds.sort(key=lambda d: d["confidence"], reverse=True)
    return preds


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tracking
# ─────────────────────────────────────────────────────────────────────────────

def _pick_active(
    detections: list[dict],
    tracker: TrackedBall,
    frame: int,
    max_jump: float = MAX_JUMP_PX,
    max_age_frames: int = 0,
) -> Optional[dict]:
    """
    Given multiple detections in a frame, return the one most likely to be
    the active ball by proximity to the predicted trajectory position.

    If the track is stale (last detection older than max_age_frames) the
    prediction is discarded and the highest-confidence detection is accepted
    directly — this prevents stale extrapolation from poisoning new tracks.
    """
    if not detections:
        return None

    # Check if the existing track is too old to trust
    track_stale = (
        max_age_frames > 0
        and tracker.last is not None
        and (frame - tracker.last.frame) > max_age_frames
    )

    predicted = None if track_stale else tracker.predict_next(frame)

    if predicted is None:
        # No track or stale track — accept the highest-confidence detection
        return detections[0]

    px, py = predicted
    best = None
    best_dist = float("inf")
    for d in detections:
        dist = np.hypot(d["x"] - px, d["y"] - py)
        if dist < best_dist:
            best_dist = dist
            best = d

    if best_dist > max_jump:
        # Too far from predicted position — treat as new track segment
        return detections[0]
    return best


def track_ball(
    video_path: str,
    api_key: str,
    sample_every: int = SAMPLE_EVERY,
    on_progress=None,
) -> TrackedBall:
    """
    Run detection on every sample_every frame and build a trajectory for
    the active ball, ignoring stationary spare balls.

    on_progress, when given, is called with the fraction of frames processed
    (0-1) roughly every 1% of the video. Callback errors are swallowed —
    reporting must never break tracking.

    Returns a TrackedBall with all confirmed positions.
    """
    import cv2

    model = _load_model(api_key)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info("Tracking ball: %d frames @ %.1f fps (sample_every=%d)", total_frames, fps, sample_every)

    # Scale the jump threshold by the sampling interval so fast-moving balls
    # aren't rejected when sample_every is large (e.g. 10 vs the default 3).
    max_jump      = MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)
    # After this many frames without a detection, treat the track as lost:
    # predict_next returns None and the next detection starts a fresh segment.
    max_age_frames = MAX_MISS * sample_every

    tracker   = TrackedBall()
    frame_idx = 0
    # Report at most ~100 times per video, only from sampled frames.
    report_every = max(sample_every, (total_frames // 100 // sample_every or 1) * sample_every)

    # We only run inference on every sample_every-th frame, so only those need
    # to be decoded. cap.read() = grab()+retrieve() decodes every frame; for
    # skipped frames grab() advances the demuxer WITHOUT decoding pixels, which
    # is ~10x cheaper. Output is unchanged — the same frames are still inferred
    # on. Measured: removes ~38% of GPU wall-clock (all wasted decode). (CF-42)
    while True:
        if frame_idx % sample_every == 0:
            ret, frame = cap.read()
            if not ret:
                break
            detections = _detect_frame(model, frame)
            active = _pick_active(detections, tracker, frame_idx,
                                  max_jump=max_jump, max_age_frames=max_age_frames)

            if active:
                tracker.misses = 0
                tracker.positions.append(BallPosition(
                    frame=frame_idx,
                    time=frame_idx / fps,
                    x=active["x"],
                    y=active["y"],
                    confidence=active["confidence"],
                ))
            else:
                tracker.misses += 1

            if on_progress and total_frames > 0 and frame_idx % report_every == 0:
                try:
                    on_progress(frame_idx / total_frames)
                except Exception:
                    logger.warning("Progress callback failed", exc_info=True)
        else:
            if not cap.grab():
                break

        frame_idx += 1

    cap.release()
    logger.info("Tracked %d ball positions", len(tracker.positions))
    return tracker


# ─────────────────────────────────────────────────────────────────────────────
# 3. Contact detection
# ─────────────────────────────────────────────────────────────────────────────

def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """Angle in degrees between two 2-D vectors."""
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    cos_theta = np.dot(v1, v2) / (n1 * n2)
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_theta)))


def classify_contact_action(
    positions: list[BallPosition],
    i: int,
    frame_height: int,
    *,
    normalize: bool = True,
) -> tuple[str, float]:
    """
    Classify a volleyball action from ball trajectory at contact index i.

    Uses the velocity vectors immediately before and after the contact point,
    plus the ball's height in the frame, to determine what kind of hit occurred.

    frame_height is required, not decorative, and it does two separate jobs. It
    divides the contact height into a frame fraction, which it always did and
    which was already resolution-independent; CF-174 added the second job,
    normalizing the velocities into REFERENCE_FRAME_HEIGHT space so the same
    physical hit gets the same label at any resolution — up to the clamp point,
    past which _scale_for reverts to unscaled and the velocity half reverts with
    it. The labels there are `main`'s, not normalized ones; see _scale_for. The
    y_frac half is unaffected by the clamp, having never been scaled.

    In image coordinates Y increases downward:
      vy > 0  = ball falling
      vy < 0  = ball rising

    Returns (action_type, confidence).
    """
    pos    = positions[i]
    y_frac = pos.y / max(frame_height, 1)   # 0 = top of frame, 1 = bottom

    # Stable velocity (px/s): average over up to 2 positions either side
    pre  = max(0, i - 2)
    post = min(len(positions) - 1, i + 2)

    a, b = positions[pre], pos
    dt = b.time - a.time
    v_before = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    a, b = pos, positions[post]
    dt = b.time - a.time
    v_after = ((b.x - a.x) / dt, (b.y - a.y) / dt) if dt > 0 else (0.0, 0.0)

    # CF-174: the thresholds below are px/s in REFERENCE_FRAME_HEIGHT space, so
    # normalize this video's velocities into it rather than scaling six
    # constants. Without this the same physical hit changes label with
    # resolution — measured, a 0.10 -> 0.25 frame-heights/s redirect reads as
    # "set" at 360p and "spike" at 1080p — and the scaled gate in find_contacts
    # makes it worse than an independent bug: at 1080p that gate admits nothing
    # under 720 px/s, so the SET band (45-240) below is unreachable by
    # construction.
    #
    # The divisor is _scale_for's, not the raw frame_height ratio, because that
    # unreachability argument runs the other way too. Past the clamp point the
    # gate reverts to unscaled and admits from 240 px/s native while a true
    # ratio would still divide by 6, landing the entire admissible band inside
    # SET — every 4K rally labelled "set" with empty labels and a flat 0.58 into
    # score.py. Whatever space the gate admitted a contact in, the classifier
    # judges it in: one policy, so the two halves cannot disagree about what a
    # px/s means. Above the clamp that means `main`'s labels, which is the same
    # trade the gate makes there and is recorded on CF-229.
    to_ref    = 1.0 / _scale_for(frame_height, log=False, normalize=normalize)
    sp_before = np.hypot(*v_before) * to_ref
    sp_after  = np.hypot(*v_after)  * to_ref
    vy_before = v_before[1] * to_ref
    vy_after  = v_after[1]  * to_ref

    # Thresholds in px/s at REFERENCE_FRAME_HEIGHT (tuned values × 30 from the
    # original 30fps px/frame)
    # SPIKE: high contact in frame, ball driven hard downward
    if y_frac < 0.45 and vy_after > 60.0 and sp_after > 180.0:
        conf = min(0.88, 0.65 + sp_after / 1500.0)
        return "spike", round(conf, 2)

    # BLOCK: high contact, ball reversed from falling to rising (spike blocked back)
    if y_frac < 0.45 and vy_before > 30.0 and vy_after < -30.0:
        return "block", 0.72

    # DIG: low contact, ball was falling, now rising (floor save)
    if y_frac > 0.58 and vy_before > 30.0 and vy_after < -30.0:
        return "dig", 0.75 if sp_after > 90.0 else 0.60

    # SERVE: ball nearly stationary before contact (toss), then driven fast
    if sp_before < 90.0 and sp_after > 180.0:
        return "serve", 0.68

    # SET: controlled mid-height redirect at moderate speed
    if 0.20 < y_frac < 0.65 and 45.0 < sp_after < 240.0:
        return "set", 0.58

    return "unknown", 0.42


def _scale_for(frame_height: int, *, log: bool = True, normalize: bool = True) -> float:
    """
    Multiplier turning the module's 360p-tuned px/s constants into thresholds
    for footage of this height (CF-174).

    log=False asks the same question without the commentary, for callers that
    run per contact rather than per video (classify_contact_action). Every
    caller must get its scale from here: the one thing worse than a wrong scale
    is two halves of the pipeline disagreeing about it.

    normalize=False returns 1.0 unconditionally — `main`'s behaviour, gate and
    labels together. It is the off position of the CF-174 kill switch
    (`BALL_CONTACT_SCALE_ENABLED`, threaded from tasks.py), and it exists
    because this change moves highlight selection on all non-360p footage with
    no ground truth to measure that against: MIN_RALLY_CONTACTS gates hard at 3,
    so a rally that loses a contact leaves highlights entirely. Without a switch
    the only response to that showing up in production is a code deploy, unlike
    every neighbouring condense knob. It runs through here rather than at the
    call sites so the gate and the classifier cannot end up on opposite sides of
    it. Silent, not warned: an operator who set it meant it, and this is called
    once per contact.

    frame_height <= 0 means the caller did not know it; fall back to the
    reference (no scaling, i.e. pre-CF-174 behaviour) and say so, because
    silently applying 360p thresholds to a 1080p video is the bug this exists
    to prevent.

    Three ranges, and the last one is the interesting one:

      <= MAX_VALIDATED_FRAME_HEIGHT   scale normally; measured on test1/test2
      up to the clamp point           scale normally, but warn — the floor is
                                      still right, the ceiling is closing in
      past the clamp point            return 1.0, i.e. pre-CF-174 behaviour

    The clamp point is where CONTACT_HIT_SPEED_PXPS * scale would reach
    CONTACT_SPEED_CEILING_FRAC of SEG_MAX_SPEED_PXPS. Freezing the scale there
    keeps the two gates from becoming disjoint, but a frozen scale under a
    growing frame is not a weaker normalization — it is the wrong one, and it
    ends up narrower than no normalization at all. So past that height this
    reverts rather than extrapolating; see the comment at the return for why
    that is the honest interim rather than a fix.
    """
    if not normalize:
        return 1.0

    if frame_height <= 0:
        if log:
            logger.warning(
                "contact thresholds requested without a frame height — assuming %.0fpx "
                "tracking space; px/s thresholds will be wrong on other resolutions",
                REFERENCE_FRAME_HEIGHT,
            )
        return 1.0

    scale        = frame_height / REFERENCE_FRAME_HEIGHT
    max_scale    = CONTACT_SPEED_CEILING_FRAC * SEG_MAX_SPEED_PXPS / CONTACT_HIT_SPEED_PXPS
    clamp_height = max_scale * REFERENCE_FRAME_HEIGHT

    if frame_height > clamp_height:
        # Past the clamp, normalizing is worse than not normalizing, so stop.
        #
        # Below this height the scale still tracks resolution, which keeps the
        # floor at a constant 0.667 frame-heights/s — the value measured good on
        # test1/test2 — and only the ceiling closes in. Above it the scale is
        # frozen while the frame keeps growing, so the whole band slides *down*
        # in physical terms: 0.444-0.556 fh/s at 2160p, under the 0.3-0.9 where
        # real play lives. A clamped scale is not a weaker normalization, it is
        # the wrong one.
        #
        # Unscaled thresholds at 2160p give a band of 0.111-0.556 fh/s. Also
        # wrong, and over-firing rather than silent — but strictly wider, and it
        # is what `main` does today, so *this gate* cannot be a regression at any
        # resolution.
        #
        # That claim stops at find_contacts; it is not a claim about the condense
        # path. dead_time.bridge_windows_by_motion scales unconditionally, and
        # correctly so (one-sided threshold, no ceiling to collide with), which
        # means above this clamp point the pipeline runs `main`'s contact set
        # joined by a 6x-tightened bridge at 2160p: fewer gaps bridged, windows
        # `main` joined left split. That pairing is new in CF-174 and is measured
        # nowhere — every dead-time fixture is 1080p or shorter. It tightens
        # rather than widens, which is the safer direction, but it is a real
        # behaviour change at the resolution phones shoot. Measuring it needs a
        # 4K fixture and is its own card.
        #
        # Reverting is the honest interim until SEG_MAX_SPEED_PXPS
        # can scale (CF-229) and the clamp stops being needed at all.
        #
        # The discontinuity at this height is real and deliberate: thresholds
        # jump 4x across one pixel. It is the price of refusing to extrapolate
        # past the evidence, and it is logged rather than smoothed over.
        #
        # Both bands are printed, in that order, because someone reading this
        # line is debugging "no contacts at 4K" and needs to know which numbers
        # are live. The clamped one is the *reason* and is not applied; the
        # unscaled one is what the detector is actually running.
        if log:
            logger.error(
                "frame_height %d is past the %.0fp clamp point, where the contact "
                "scale stops tracking resolution and its band (%.3f-%.3f "
                "frame-heights/s) would drop below real play. Reverting to UNSCALED "
                "thresholds — the pre-CF-174 behaviour, which over-fires but is not "
                "narrower than it. IN FORCE HERE: %.3f-%.3f frame-heights/s "
                "(%.0f-%.0f px/s). Contact detection is not normalized at this "
                "resolution; scaling SEG_MAX_SPEED_PXPS is the fix (CF-229).",
                frame_height, clamp_height,
                CONTACT_HIT_SPEED_PXPS * max_scale / frame_height,
                SEG_MAX_SPEED_PXPS / frame_height,
                CONTACT_HIT_SPEED_PXPS / frame_height,
                SEG_MAX_SPEED_PXPS / frame_height,
                CONTACT_HIT_SPEED_PXPS, SEG_MAX_SPEED_PXPS,
            )
        return 1.0

    if frame_height > MAX_VALIDATED_FRAME_HEIGHT:
        # Not "under-normalized" — quantify it, because the number is the point.
        # SEG_MAX_SPEED_PXPS does not scale, so what is left between the scaled
        # floor and the fixed ceiling is the entire band this detector can see.
        #
        # Warning, not error: this range still normalizes correctly — the floor
        # holds at 0.667 frame-heights/s, the value measured good on test1 and
        # test2 — and only the ceiling closes in, so contacts get scarcer rather
        # than wrong. The band closes continuously (1080p leaves 1.67x, 1081p
        # leaves 1.666x: the same footage in every practical sense), so there is
        # nothing here to page anyone about. The height past which normalizing
        # is actively worse than not is the clamp point, handled above.
        floor = CONTACT_HIT_SPEED_PXPS * scale
        if log:
            logger.warning(
                "frame_height %d is above the %dp CF-174 was measured on. Contact "
                "detection admits only %.3f-%.3f frame-heights/s here (%.0f-%.0f "
                "px/s, a %.2fx band vs %.2fx at %.0fp) because SEG_MAX_SPEED_PXPS "
                "does not scale; this height is unmeasured and contacts get scarcer "
                "as the band closes. Scaling the ceiling is the real fix (CF-229).",
                frame_height, MAX_VALIDATED_FRAME_HEIGHT,
                floor / frame_height, SEG_MAX_SPEED_PXPS / frame_height,
                floor, SEG_MAX_SPEED_PXPS,
                SEG_MAX_SPEED_PXPS / floor,
                # Derived, not literal: this is the reference band, and it moves
                # whenever either constant is tuned (CF-103 lowers the floor to
                # 220 and it becomes 5.45x). It is the one number in this line a
                # reader cannot cross-check against the others.
                SEG_MAX_SPEED_PXPS / CONTACT_HIT_SPEED_PXPS,
                REFERENCE_FRAME_HEIGHT,
            )
    return scale


def _segment_track(positions: list[BallPosition]) -> list[list[BallPosition]]:
    """
    Split the raw track into coherent single-ball segments.

    The tracker hops between the game ball, spare balls, and false positives,
    so the positions list is a chimera of multiple objects. Split wherever the
    implied speed exceeds SEG_MAX_SPEED_PXPF (teleport = different object) or
    the detection gap exceeds MAX_SEGMENT_GAP_FRAMES, then drop segments that
    are too short (flicker) or near-stationary (held/spare balls).

    These two thresholds are deliberately NOT frame-height-scaled — see the
    segmentation config block for the measurements behind that.
    """
    if not positions:
        return []

    raw: list[list[BallPosition]] = []
    cur = [positions[0]]
    for a, b in zip(positions, positions[1:]):
        dt = b.time - a.time
        if dt <= 0:
            continue
        speed = np.hypot(b.x - a.x, b.y - a.y) / dt  # px/s
        if dt > MAX_SAMPLE_GAP_SEC or speed > SEG_MAX_SPEED_PXPS:
            raw.append(cur)
            cur = [b]
        else:
            cur.append(b)
    raw.append(cur)

    kept = []
    for seg in raw:
        if len(seg) < SEG_MIN_POSITIONS:
            continue
        speeds = [
            np.hypot(b.x - a.x, b.y - a.y) / max(b.time - a.time, 1e-6)
            for a, b in zip(seg, seg[1:])
        ]
        if np.median(speeds) < SEG_MIN_MEDIAN_SPEED_PXPS:
            continue
        kept.append(seg)

    logger.info(
        "Track segmentation: %d positions → %d segments, %d kept (%d positions)",
        len(positions), len(raw), len(kept), sum(len(s) for s in kept),
    )
    return kept


def _estimate_gravity(segments: list[list[BallPosition]]) -> float:
    """
    Estimate gravity in px/s² from coherent flight segments.

    Median vertical acceleration across all within-segment triples. Free
    flight dominates (contacts are rare), so the median is robust regardless
    of camera distance/zoom. Clamped to >= 0 (image Y grows downward).
    """
    accels = []
    for seg in segments:
        for i in range(1, len(seg) - 1):
            dt_b = seg[i].time - seg[i - 1].time
            dt_a = seg[i + 1].time - seg[i].time
            if dt_b <= 0 or dt_a <= 0:
                continue
            vy_b = (seg[i].y - seg[i - 1].y) / dt_b
            vy_a = (seg[i + 1].y - seg[i].y) / dt_a
            accels.append((vy_a - vy_b) / ((dt_b + dt_a) / 2))
    if not accels:
        return 0.0
    return max(float(np.median(accels)), 0.0)


def find_contacts(
    tracker: TrackedBall, frame_height: int = 0, *, normalize: bool = True
) -> list[dict]:
    """
    Find player contacts: deviations from ballistic flight within coherent
    track segments.

    Pipeline: segment the raw track (drop hops/junk) → estimate gravity from
    the segments → within each segment, flag samples whose post-velocity
    deviates from the ballistic prediction by more than the noise floor
    (CONTACT_RESIDUAL_MIN_PXPS, or CONTACT_RESIDUAL_RATIO × speed if higher)
    with plausible hit speed on at least one side → global time-sorted
    debounce (MIN_CONTACT_SPACING). All velocities are px/s, so behaviour is
    identical across source frame rates.

    Thresholds are tuned against real footage; see constants at module top.

    frame_height is the tracking space of these positions. It scales the px/s
    thresholds (CF-174) and enables trajectory-based action classification.
    Omitting it assumes REFERENCE_FRAME_HEIGHT and warns — on 1080p footage
    that silently over-fires the detector.

    normalize=False turns the CF-174 scaling off and restores `main`'s gate and
    `main`'s labels, while frame_height keeps doing its unscaled job (the
    classifier's frame fraction). It is a runtime kill switch, not a tuning
    knob — see _scale_for — and it defaults to on: production passes
    settings.ball_contact_scale_enabled, which ships True.

    Returns list of {time, frame, x, y, angle_change, speed_change,
    speed_before, speed_after, residual, action, action_confidence}.
    """
    scale = _scale_for(frame_height, normalize=normalize)
    hit_speed     = CONTACT_HIT_SPEED_PXPS * scale
    residual_min  = CONTACT_RESIDUAL_MIN_PXPS * scale

    segments = _segment_track(tracker.positions)
    if not segments:
        # Both routes here look identical downstream — no contacts, condense
        # then cuts everything — so the log has to separate them, and blaming
        # the filters for a rejection that never happened sends the operator
        # tuning SEG_* at a tracking outage. _segment_track returns [] for an
        # empty position list before any threshold is consulted.
        if not tracker.positions:
            logger.warning(
                "find_contacts: the track is empty (0 positions) — the ball was "
                "never detected, so nothing reached the segmentation filters"
            )
        else:
            # Every segment was rejected as flicker or near-stationary, so name
            # the filter that ate the track.
            logger.warning(
                "find_contacts: _segment_track kept no segments from %d positions "
                "(SEG_MIN_POSITIONS=%d, SEG_MIN_MEDIAN_SPEED_PXPS=%.0f, both unscaled)",
                len(tracker.positions), SEG_MIN_POSITIONS, SEG_MIN_MEDIAN_SPEED_PXPS,
            )
        return []

    g_px = _estimate_gravity(segments)
    logger.info("Estimated gravity: %.3f px/s²", g_px)

    candidates: list[dict] = []

    for seg in segments:
        for i in range(1, len(seg) - 1):
            prev, curr, nxt = seg[i - 1], seg[i], seg[i + 1]

            dt_before = curr.time - prev.time
            dt_after  = nxt.time  - curr.time
            if dt_before <= 0 or dt_after <= 0:
                continue

            v_before = ((curr.x - prev.x) / dt_before, (curr.y - prev.y) / dt_before)
            v_after  = ((nxt.x  - curr.x) / dt_after,  (nxt.y  - curr.y) / dt_after)

            speed_before = np.hypot(*v_before)
            speed_after  = np.hypot(*v_after)

            # A real hit imparts speed — both sides slow = detector wobble
            if max(speed_before, speed_after) < hit_speed:
                continue

            # Ballistic prediction: horizontal velocity persists, vertical gains gravity
            dt_mid = (dt_before + dt_after) / 2
            predicted_vy = v_before[1] + g_px * dt_mid
            residual = float(np.hypot(v_after[0] - v_before[0], v_after[1] - predicted_vy))

            # CONTACT_RESIDUAL_RATIO needs no scaling — it multiplies a speed
            # that is already in this video's pixel space.
            threshold = max(residual_min, CONTACT_RESIDUAL_RATIO * speed_before)
            if residual < threshold:
                continue

            angle_change = _angle_between(v_before, v_after)
            speed_change = abs(speed_after - speed_before) / max(speed_before, 1e-6)

            action, action_conf = (
                classify_contact_action(seg, i, frame_height, normalize=normalize)
                if frame_height > 0
                else ("unknown", 0.42)
            )
            candidates.append({
                "time":             curr.time,
                "frame":            curr.frame,
                "x":                curr.x,
                "y":                curr.y,
                "angle_change":     round(angle_change, 1),
                "speed_change":     round(speed_change, 3),
                "speed_before":     round(float(speed_before), 2),
                "speed_after":      round(float(speed_after), 2),
                "residual":         round(residual, 2),
                "action":           action,
                "action_confidence": action_conf,
            })

    # Global time-sorted debounce: one hit disturbs adjacent samples too
    candidates.sort(key=lambda c: c["time"])
    contacts: list[dict] = []
    last_contact_time = float("-inf")
    for c in candidates:
        if c["time"] - last_contact_time < MIN_CONTACT_SPACING:
            continue
        last_contact_time = c["time"]
        contacts.append(c)

    logger.info(
        "Found %d contacts in trajectory of %d positions",
        len(contacts), len(tracker.positions),
    )
    if not contacts:
        # An empty return is indistinguishable downstream from "no ball in this
        # video": condense falls back, highlights emit nothing, and neither says
        # why. Print the gates that rejected everything so the resolution case
        # (CF-174) is readable in a worker log instead of needing a repro.
        logger.warning(
            "find_contacts found nothing in %d segments (%d positions) at "
            "frame_height=%d: scale %.2f, gates hit_speed %.0f / residual_min "
            "%.0f px/s against a segmentation ceiling of %.0f px/s",
            len(segments), len(tracker.positions), frame_height, scale,
            hit_speed, residual_min, SEG_MAX_SPEED_PXPS,
        )
    return contacts


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rally clipping
# ─────────────────────────────────────────────────────────────────────────────

def _make_rally(seg: list[dict], video_duration: float, frame_height: int = 0) -> dict:
    """Build a rally clip dict from a list of contacts."""
    action_scores: dict[str, float] = {}
    action_counts: dict[str, int] = {}
    for c in seg:
        a = c.get("action", "unknown")
        if a != "unknown":
            action_scores[a] = action_scores.get(a, 0.0) + c.get("action_confidence", 0.0)
            action_counts[a] = action_counts.get(a, 0) + 1

    if action_scores:
        dominant = max(action_scores, key=action_scores.__getitem__)
        # Divide by contacts that contributed to dominant action (not total contacts)
        avg_conf = round(action_scores[dominant] / max(action_counts[dominant], 1), 3)
        seen: dict[str, None] = {}
        for c in seg:
            a = c.get("action", "unknown")
            if a != "unknown":
                seen[a] = None
        labels = list(seen.keys())
    else:
        dominant, avg_conf, labels = "unknown", 0.50, []

    first_contact = seg[0]["time"]
    last_contact  = seg[-1]["time"]

    # Rally features for downstream highlight scoring (ml/pipeline/score.py).
    # Speeds are normalized to frame-heights per SECOND so they are comparable
    # across both resolutions and frame rates.
    speed_div = float(frame_height) if frame_height > 0 else 1.0
    max_speed = max(
        (max(c.get("speed_before", 0.0), c.get("speed_after", 0.0)) for c in seg),
        default=0.0,
    ) / speed_div
    sharp_changes = sum(1 for c in seg if c.get("angle_change", 0.0) >= 60.0)
    floor_bounce = any(
        c.get("angle_change", 0.0) >= FLOOR_BOUNCE_ANGLE
        and frame_height > 0
        and c.get("y", 0.0) / frame_height >= FLOOR_BOUNCE_Y_FRAC
        for c in seg
    )
    contact_span = max(last_contact - first_contact, 1e-6)

    return {
        "start":      max(0.0, first_contact - PRE_RALLY_PAD),
        "end":        min(video_duration, last_contact + POST_PLAY_PAD),
        "action":     dominant,
        "confidence": avg_conf,
        "labels":     labels,
        "features": {
            "contact_count":  len(seg),
            "first_contact":  round(first_contact, 2),
            "last_contact":   round(last_contact, 2),
            "duration":       round(contact_span, 2),
            "max_speed":      round(max_speed, 4),
            "sharp_changes":  sharp_changes,
            "floor_bounce":   floor_bounce,
            "contact_rate":   round(len(seg) / contact_span, 3),
        },
    }


def contacts_to_rallies(
    contacts: list[dict],
    video_duration: float,
    frame_height: int,
) -> list[dict]:
    """
    Convert a contact list into rally clip boundaries.

    Algorithm:
      1. Group contacts by time gap: a new segment starts when the gap to the
         previous contact exceeds RALLY_GAP_SECONDS.
      2. Segments longer than MAX_CLIP_DURATION are subdivided on their largest
         internal gaps so each sub-clip stays under the cap.
      3. Each segment becomes one clip:
           rally_start = first_contact.time - PRE_RALLY_PAD  (>= 0)
           rally_end   = last_contact.time  + POST_PLAY_PAD  (<= video_duration)
      4. Clips shorter than MIN_RALLY_DURATION are discarded as noise.

    Returns list of dicts compatible with generate_clips():
      {start, end, action, confidence, labels}
    """
    if not contacts:
        return []

    sorted_contacts = sorted(contacts, key=lambda c: c["time"])

    # ── 1. Group by large time gaps ───────────────────────────────────────────
    groups: list[list[dict]] = []
    current: list[dict] = [sorted_contacts[0]]
    for c in sorted_contacts[1:]:
        if c["time"] - current[-1]["time"] > RALLY_GAP_SECONDS:
            groups.append(current)
            current = [c]
        else:
            current.append(c)
    groups.append(current)

    # ── 2. Sub-divide groups that are too long ────────────────────────────────
    final_segments: list[list[dict]] = []
    for grp in groups:
        span = grp[-1]["time"] - grp[0]["time"]
        if span <= MAX_CLIP_DURATION or len(grp) < 4:
            final_segments.append(grp)
            continue

        # Repeatedly split on the largest internal gap until all sub-groups fit
        pending = [grp]
        while pending:
            seg = pending.pop()
            span = seg[-1]["time"] - seg[0]["time"]
            if span <= MAX_CLIP_DURATION or len(seg) < 4:
                final_segments.append(seg)
                continue
            # Find largest gap between consecutive contacts in this segment
            max_gap = 0.0
            split_idx = 1
            for i in range(1, len(seg)):
                g = seg[i]["time"] - seg[i - 1]["time"]
                if g > max_gap:
                    max_gap = g
                    split_idx = i
            pending.append(seg[:split_idx])
            pending.append(seg[split_idx:])

    # ── 3 & 4. Build rally windows, discard noise ─────────────────────────────
    rallies: list[dict] = []
    for seg in sorted(final_segments, key=lambda s: s[0]["time"]):
        if len(seg) < MIN_RALLY_CONTACTS:
            continue  # 1-2 isolated contacts = noise, not a rally
        r = _make_rally(seg, video_duration, frame_height)
        if r["end"] - r["start"] >= MIN_RALLY_DURATION:
            rallies.append(r)

    logger.info(
        "contacts_to_rallies: %d contacts -> %d rallies",
        len(contacts), len(rallies),
    )
    return rallies


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def _read_frame_height(video_path: str) -> int:
    """
    Frame height of a video, or 0 if it cannot be read — which _scale_for
    already treats as "caller did not know" and warns about.

    The 0 return is reachable in practice only for a file that opens but
    reports no height; detect_contacts runs track_ball first, which raises on
    a file that will not open at all.
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            return 0
        return int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()


def detect_contacts(
    video_path: str,
    api_key: str | None = None,
    sample_every: int = SAMPLE_EVERY,
) -> list[dict]:
    """
    Full pipeline: detect ball -> track trajectory -> find contacts.

    Returns list of contact dicts with keys:
      time, frame, x, y, angle_change, speed_change

    api_key defaults to the ROBOFLOW_API_KEY environment variable.
    sample_every: run inference every N frames (higher = faster, less precise).
    """
    key = api_key or os.environ.get("ROBOFLOW_API_KEY", "")
    if not key:
        raise ValueError("ROBOFLOW_API_KEY not set and api_key not provided")

    tracker  = track_ball(video_path, key, sample_every=sample_every)
    contacts = find_contacts(tracker, _read_frame_height(video_path))
    return contacts


def detect_rallies(
    video_path: str,
    api_key: str | None = None,
    sample_every: int = SAMPLE_EVERY,
) -> list[dict]:
    """
    Full pipeline: detect ball -> track -> contacts -> rally clip boundaries.

    Returns list of rally dicts ready for generate_clips():
      {start, end, action, confidence, labels}
    """
    key = api_key or os.environ.get("ROBOFLOW_API_KEY", "")
    if not key:
        raise ValueError("ROBOFLOW_API_KEY not set and api_key not provided")

    import cv2

    cap = cv2.VideoCapture(video_path)
    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    video_duration = total_frames / fps

    tracker  = track_ball(video_path, key, sample_every=sample_every)
    contacts = find_contacts(tracker, frame_height)
    rallies  = contacts_to_rallies(contacts, video_duration, frame_height)
    return rallies
