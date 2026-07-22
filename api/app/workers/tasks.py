"""Celery tasks for async video processing."""
import hashlib
import json
import logging
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _file_md5(path: Path) -> str:
    """Content hash of a file (streamed, constant memory)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _modal_configured() -> bool:
    from app.config import settings
    return bool(settings.modal_token_id and settings.modal_token_secret)


def _track_ball_modal(local_video: Path, r2_key: str, sample_every: int):
    """
    Run ball tracking on Modal GPU (CF-11): ~27-42 min CPU -> low single-digit
    minutes on a T4. Raises on any failure; caller falls back to local CPU.
    """
    import os
    import modal as modal_sdk
    from ml.pipeline.ball import TrackedBall, BallPosition
    from app.services import storage as s3

    video_url = s3.presign_url(r2_key, expires_in=3600)
    fn = modal_sdk.Function.from_name("clipfarm-ball-tracking", "track_ball_remote")
    positions = fn.remote(video_url, os.environ["ROBOFLOW_API_KEY"], sample_every)
    return TrackedBall(positions=[BallPosition(**p) for p in positions])


def _track_ball_cached(local_video: Path, tmp: Path, sample_every: int, r2_key: str = ""):
    """
    Ball tracking with an R2-backed cache keyed by video content hash.

    Tracking a 22-min video takes ~30 min on CPU; the positions only depend
    on the video bytes, the model version, and the sample rate — so re-runs
    of the same footage (re-uploads, pipeline tuning) load cached positions
    in seconds instead. Cache failures fall through to normal tracking.

    When Modal is configured, tracking itself runs on a GPU worker (CF-11)
    instead of locally on CPU; Modal failures fall back to local CPU tracking
    so a Modal outage never blocks processing.
    """
    import os
    from app.services import storage as s3
    from ml.pipeline.ball import track_ball, TrackedBall, BallPosition, MODEL_ID

    model_slug = MODEL_ID.replace("/", "-")
    cache_key = f"ball-cache/{_file_md5(local_video)}-{model_slug}-s{sample_every}.json"
    cache_path = tmp / "ball_cache.json"

    try:
        s3.download_file(cache_key, cache_path)
        data = json.loads(cache_path.read_text())
        cached = TrackedBall(positions=[BallPosition(**p) for p in data["positions"]])
        logger.info("Ball cache hit (%s): %d positions", cache_key, len(cached.positions))
        return cached
    except Exception:
        logger.info("Ball cache miss (%s) — running tracking", cache_key)

    tracker: TrackedBall | None = None
    if _modal_configured() and r2_key:
        try:
            tracker = _track_ball_modal(local_video, r2_key, sample_every)
            logger.info("Ball tracking ran on Modal GPU: %d positions", len(tracker.positions))
        except Exception as modal_err:
            logger.warning("Modal ball tracking failed (%s) — falling back to local CPU", modal_err)

    if tracker is None:
        tracker = track_ball(str(local_video), os.environ["ROBOFLOW_API_KEY"], sample_every=sample_every)

    try:
        cache_path.write_text(json.dumps({"positions": [asdict(p) for p in tracker.positions]}))
        s3.upload_file(cache_path, cache_key, "application/json")
        logger.info("Ball positions cached to %s", cache_key)
    except Exception as cache_err:
        logger.warning("Failed to write ball cache (%s)", cache_err)

    return tracker


@celery_app.task(bind=True, name="recut_clip", max_retries=2, default_retry_delay=30)
def recut_clip_task(self, clip_id: str, game_id: str, raw_video_url: str, start: float, end: float):
    """Re-cut a single clip from the source video after a trim adjustment."""
    from app.workers._sync_db import sync_update_clip_url
    from app.services import storage as s3
    from ml.pipeline.clip import recut_single

    cid = uuid.UUID(clip_id)
    gid = uuid.UUID(game_id)
    r2_key = urlparse(raw_video_url).path.lstrip("/")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_video = tmp / "game.mp4"
            logger.info("Downloading source video for recut of clip %s", clip_id)
            s3.download_file(r2_key, local_video)

            clip_path, thumb_path = recut_single(str(local_video), start, end, tmp)

            # Upload new clip + thumbnail
            clip_url = s3.upload_file(clip_path, s3.clip_key(gid, cid), "video/mp4")
            thumb_url = None
            if thumb_path:
                thumb_url = s3.upload_file(thumb_path, s3.thumbnail_key(gid, cid), "image/jpeg")

            sync_update_clip_url(cid, clip_url, thumb_url)
            logger.info("Recut complete for clip %s", clip_id)
    except Exception as exc:
        logger.exception("Recut failed for clip %s", clip_id)
        raise self.retry(exc=exc)


def _run_detection_local(video_path: str) -> list[dict]:
    """Fallback: run detection locally (CPU, slow)."""
    from ml.pipeline.detect import run_detection
    return run_detection(video_path)


@celery_app.task(bind=True, name="process_game", max_retries=2, default_retry_delay=60)
def process_game_task(self, game_id: str, raw_video_url: str, condense: bool = False):
    """
    Main processing pipeline:
    1. Download video, extract audio energy envelope (cheap, used by 3 stages)
    2. Ball tracking → contacts with trajectory-based action labels → rally
       windows with shape features (contact count, speed, floor bounce, ...)
    3. Highlight scoring: post-rally cheer + rally shape (+ CLIP frames when
       enabled) → highlight_score per rally; low scorers dropped here
    4. Pose classification — only on rallies that survived scoring
    5. Audio confidence weighting, generate + upload clips, persist to DB
    6. Optional (condense=True): stitch a dead-time-removed video from the
       pre-gate rally signal — all rallies, not just highlights

    Falls back to pose-first pipeline if ROBOFLOW_API_KEY is not set.
    """
    from app.workers._sync_db import (
        sync_set_game_status,
        sync_save_clips,
        sync_delete_game_clips,
    )
    from app.services import storage as s3
    import cv2 as _cv2
    import os as _os

    gid = uuid.UUID(game_id)
    r2_key = urlparse(raw_video_url).path.lstrip("/")
    logger.info("Starting processing for game %s", game_id)

    try:
        sync_set_game_status(gid, "processing")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_video = tmp / "game.mp4"
            logger.info("Downloading key=%s from R2", r2_key)
            s3.download_file(r2_key, local_video)

            # Video metadata — used by all stages below
            _cap = _cv2.VideoCapture(str(local_video))
            _fps         = _cap.get(_cv2.CAP_PROP_FPS) or 30.0
            _frames      = int(_cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            _frame_h     = int(_cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
            _cap.release()
            video_duration = _frames / _fps

            # ── Stage 0: Audio energy envelope ────────────────────────────
            # Extracted once (seconds, even for a full game) and reused by
            # cheer scoring and confidence weighting below.
            audio_energy = None
            try:
                from ml.pipeline.audio import compute_audio_energy
                audio_energy = compute_audio_energy(str(local_video))
                if audio_energy is None:
                    logger.warning("No usable audio track — cheer scoring disabled")
            except Exception as audio_err:
                logger.warning("Audio extraction failed (%s)", audio_err)

            # ── Stage 1: Ball tracking → rally windows ────────────────────
            # Primary pipeline: Roboflow ball model detects every contact,
            # trajectory physics classify the action, contacts are grouped
            # into rally windows. Fast, occlusion-proof, no GPU needed.
            detections: list[dict] = []
            ball_ok = False
            ball_contacts: list[dict] = []
            ball_positions: list[dict] = []
            if _os.environ.get("ROBOFLOW_API_KEY"):
                try:
                    from ml.pipeline.ball import find_contacts, contacts_to_rallies
                    # fps-aware sampling: ~3 ball detections per second of video
                    # regardless of source frame rate (tuned at 30fps/every-10th;
                    # a 60fps video would otherwise double inference cost).
                    sample_every = max(1, round(_fps / 3.0))
                    tracker   = _track_ball_cached(local_video, tmp, sample_every=sample_every, r2_key=r2_key)
                    contacts  = find_contacts(tracker, frame_height=_frame_h)
                    detections = contacts_to_rallies(contacts, video_duration, _frame_h)
                    ball_ok   = True
                    ball_contacts = contacts
                    # Kept for the condense stage's motion bridge (CF-46).
                    if condense:
                        ball_positions = [
                            {"time": p.time, "x": p.x, "y": p.y} for p in tracker.positions
                        ]
                    logger.info("Ball pipeline: %d contacts → %d rallies", len(contacts), len(detections))
                except Exception as ball_err:
                    logger.warning("Ball pipeline failed (%s) — falling back to pose-first", ball_err)

            if not ball_ok:
                # Fallback: pose-first local CPU scan (Ball tracking failed
                # entirely or ROBOFLOW_API_KEY unset — rare path)
                detections = _run_detection_local(str(local_video))

                try:
                    from ml.pipeline.detect import group_into_rallies
                    before = len(detections)
                    detections = group_into_rallies(detections, video_duration)
                    logger.info("Rally grouping: %d → %d clips", before, len(detections))
                except Exception as rally_err:
                    logger.warning("Rally grouping failed (%s)", rally_err)

            # Snapshot the pre-gate rally windows: the condense stage must
            # keep every rally, not just the ones the highlight gate favors.
            pre_gate_rallies = [dict(d) for d in detections]

            # ── Stage 2: Highlight scoring → drop low scorers ─────────────
            # Cheer reaction after the rally + rally shape features (+ CLIP
            # frames when enabled) → highlight_score. This is the precision
            # gate: only rallies worth watching go on to pose and cutting.
            from app.config import settings as app_settings
            try:
                if audio_energy is not None:
                    from ml.pipeline.audio import score_cheers
                    detections = score_cheers(detections, *audio_energy)
                from ml.pipeline.score import score_highlights
                detections = score_highlights(
                    str(local_video), detections,
                    use_clip=app_settings.clip_verify_enabled,
                )
                before = len(detections)
                threshold = app_settings.highlight_score_threshold
                detections = [d for d in detections if d["highlight_score"] >= threshold]
                logger.info(
                    "Highlight gate (>= %.2f): %d → %d rallies",
                    threshold, before, len(detections),
                )
            except Exception as score_err:
                logger.warning("Highlight scoring failed (%s) — keeping all rallies", score_err)

            # ── Stage 3: Pose within surviving windows — refine labels ────
            # Runs YOLOv8s-pose only inside rallies that passed the highlight
            # gate. Overrides the ball-trajectory label when pose is more
            # confident.
            try:
                from ml.pipeline.detect import classify_within_windows
                detections = classify_within_windows(
                    str(local_video), detections,
                    model_name=app_settings.pose_model,
                    imgsz=app_settings.pose_imgsz,
                    skip_frames=app_settings.pose_skip_frames,
                )
                logger.info("Pose refinement complete (%d windows)", len(detections))
            except Exception as pose_err:
                logger.warning("Pose refinement failed (%s) — keeping trajectory labels", pose_err)

            # ── Stage 4: Audio confidence weighting ──────────────────────
            # Adjusts label confidence only — precision filtering now happens
            # at the highlight gate above, so no hard confidence cut here.
            try:
                from ml.pipeline.audio import weight_detections_by_audio
                detections = weight_detections_by_audio(
                    str(local_video), detections, precomputed=audio_energy,
                )
            except Exception as audio_err:
                logger.warning("Audio weighting failed (%s) — using unweighted", audio_err)

            from ml.pipeline.clip import generate_clips
            clips_data = generate_clips(str(local_video), detections, tmp)

            # ── 3. Upload clips and thumbnails, save to DB ────────────────
            rows = []
            for cd in clips_data:
                clip_id = uuid.uuid4()
                clip_url = s3.upload_file(
                    cd["clip_path"],
                    s3.clip_key(gid, clip_id),
                    "video/mp4",
                )
                thumb_url = None
                if cd.get("thumb_path"):
                    thumb_url = s3.upload_file(
                        cd["thumb_path"],
                        s3.thumbnail_key(gid, clip_id),
                        "image/jpeg",
                    )
                rows.append({
                    "id": clip_id,
                    "game_id": gid,
                    "action_type": cd["action"],
                    "confidence": cd["confidence"],
                    "highlight_score": cd.get("highlight_score"),
                    "start_time": cd["start"],
                    "end_time": cd["end"],
                    "clip_url": clip_url,
                    "thumbnail_url": thumb_url,
                    "labels": cd.get("labels", []),
                })

            # Idempotency (CF-37): a redelivered or re-enqueued task must
            # refresh this game's clips, not append a duplicate set. Clear the
            # previous run's clips now that the new ones are ready — doing it
            # here rather than at task start means a mid-pipeline failure leaves
            # the old clips intact instead of wiping them for nothing.
            stale_urls = sync_delete_game_clips(gid)
            for url in stale_urls:
                try:
                    s3.delete_file(urlparse(url).path.lstrip("/"))
                except Exception as del_err:
                    logger.warning("Stale clip cleanup failed for %s (%s)", url, del_err)
            if stale_urls:
                logger.info("Replaced %d stale clip artifacts from a prior run", len(stale_urls))

            sync_save_clips(rows)

            # ── Stage 5 (opt-in): condensed dead-time-removed video ───────
            # Derived from the pre-gate rally signal, cut + stitched with
            # ffmpeg. Never fatal: highlight clips are already saved, so a
            # condense failure only means the game page shows no condensed
            # video.
            if condense:
                try:
                    from app.workers._sync_db import sync_set_condensed_result
                    from ml.pipeline.dead_time import (
                        active_windows_from_contacts,
                        active_windows_from_detections,
                        bridge_windows_by_motion,
                    )
                    from ml.pipeline.clip import generate_condensed_video

                    if ball_ok:
                        windows = active_windows_from_contacts(
                            ball_contacts, video_duration,
                            gap_seconds=app_settings.condense_gap_seconds,
                            pad_before=app_settings.condense_pad_before,
                            pad_after=app_settings.condense_pad_after,
                            min_contacts=app_settings.condense_min_contacts,
                            merge_gap_seconds=app_settings.condense_merge_gap_seconds,
                        )
                        windows = bridge_windows_by_motion(
                            windows, ball_positions,
                            speed_pxps=app_settings.condense_bridge_speed_pxps,
                            fast_fraction=app_settings.condense_bridge_fast_fraction,
                            max_bridge_seconds=app_settings.condense_bridge_max_seconds,
                        )
                    else:
                        windows = active_windows_from_detections(
                            pre_gate_rallies, video_duration,
                            pad_before=app_settings.condense_pad_before,
                            pad_after=app_settings.condense_pad_after,
                            merge_gap_seconds=app_settings.condense_merge_gap_seconds,
                        )

                    if windows:
                        condensed_path, condensed_duration = generate_condensed_video(
                            str(local_video), windows, tmp,
                        )
                        condensed_url = s3.upload_file(
                            condensed_path, s3.condensed_key(gid), "video/mp4",
                        )
                        sync_set_condensed_result(
                            gid,
                            condensed_video_url=condensed_url,
                            original_duration=video_duration,
                            condensed_duration=condensed_duration,
                        )
                        logger.info(
                            "Condensed video: %.0fs → %.0fs (%d windows)",
                            video_duration, condensed_duration, len(windows),
                        )
                    else:
                        logger.warning("Condense requested but no active windows detected — skipping")
                except Exception as condense_err:
                    logger.exception("Condense stage failed (%s) — game proceeds without it", condense_err)

            sync_set_game_status(gid, "ready", processed_at=datetime.now(timezone.utc))
            logger.info("Done: %d clips for game %s", len(rows), game_id)

    except Exception as exc:
        logger.exception("Processing failed for game %s", game_id)
        sync_set_game_status(gid, "failed", error_message=str(exc))
        raise self.retry(exc=exc)
