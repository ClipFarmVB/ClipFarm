"""Celery tasks for async video processing."""
import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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


def _run_detection_modal(r2_key: str) -> list[dict]:
    """Call the Modal GPU function and return detections."""
    import modal
    detect_fn = modal.Function.from_name("clipfarm-detect", "detect_actions")
    return detect_fn.remote(r2_key)


def _run_detection_local(video_path: str) -> list[dict]:
    """Fallback: run detection locally (CPU, slow)."""
    from ml.pipeline.detect import run_detection
    return run_detection(video_path)


def _run_dead_time_detection_local(video_path: str) -> list[dict]:
    """Run standalone dead-time prototype and convert to clip-style detections."""
    from ml.dead_time_prototype import analyze_video

    result = analyze_video(video_path, sample_stride=4)
    detections: list[dict] = []
    for segment in result.get("segments", []):
        detections.append({
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "action": "unknown",
            "confidence": float(segment.get("score", 0.0)),
            "score": float(segment.get("score", 0.0)),
        })
    return detections


@celery_app.task(bind=True, name="process_game", max_retries=2, default_retry_delay=60)
def process_game_task(self, game_id: str, raw_video_url: str):
    """
    Main processing pipeline:
    1. Run ML detection via Modal GPU (or local fallback)
    2. Download video + generate clips with FFmpeg
    3. Upload clips to R2
    4. Persist clips to DB
    5. Update game status
    """
    from app.workers._sync_db import sync_set_game_status, sync_save_clips
    from app.services import storage as s3

    gid = uuid.UUID(game_id)
    r2_key = urlparse(raw_video_url).path.lstrip("/")
    logger.info("Starting processing for game %s", game_id)

    try:
        sync_set_game_status(gid, "processing")

        # ── 1. Run ML detection ───────────────────────────────────────────
        # Try Modal GPU first, fall back to local
        try:
            logger.info("Running detection via Modal GPU...")
            detections = _run_detection_modal(r2_key)
            logger.info("Modal returned %d detections", len(detections))
        except Exception as modal_err:
            logger.warning("Modal failed (%s), falling back to local detection", modal_err)
            # Need to download video for local detection
            with tempfile.TemporaryDirectory() as tmpdir:
                local_video = Path(tmpdir) / "game.mp4"
                s3.download_file(r2_key, local_video)
                detections = _run_detection_local(str(local_video))

        # ── 2. Download video, verify with CLIP, generate clips ─────────
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_video = tmp / "game.mp4"
            logger.info("Downloading key=%s from R2 for clip generation", r2_key)
            s3.download_file(r2_key, local_video)

            # Compute video metadata once — reused by fusion, rally grouping, etc.
            import cv2 as _cv2
            _cap = _cv2.VideoCapture(str(local_video))
            _fps = _cap.get(_cv2.CAP_PROP_FPS) or 30.0
            _frames = int(_cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            _cap.release()
            video_duration = _frames / _fps

            # ── Ball-contact fusion ───────────────────────────────────────
            # Run Roboflow ball tracking at a lower sample rate (every 10th
            # frame ≈ 3 fps from 30 fps) to keep cost manageable on long games.
            # Contacts anchor clip timing precisely and validate pose detections:
            #   - ball contact + nearby pose  → boosted confidence, better timing
            #   - strong contact, no pose     → create unknown clip (no false neg)
            #   - pose detection, no contact  → penalised (possible false pos)
            try:
                from ml.pipeline.ball import detect_contacts
                from ml.pipeline.detect import fuse_with_ball_contacts
                before_fusion = len(detections)
                ball_contacts = detect_contacts(str(local_video), sample_every=10)
                detections = fuse_with_ball_contacts(detections, ball_contacts, video_duration)
                logger.info(
                    "Ball fusion: %d contacts + %d pose → %d fused detections",
                    len(ball_contacts), before_fusion, len(detections),
                )
            except Exception as ball_err:
                logger.warning("Ball tracking/fusion failed (%s) — using pose-only", ball_err)

            # Rally grouping — merge per-action detections that belong to the
            # same rally into single longer clips, eliminating dead time between
            # consecutive actions.
            try:
                from ml.pipeline.detect import group_into_rallies
                before_rally = len(detections)
                detections = group_into_rallies(detections, video_duration)
                logger.info(
                    "Rally grouping: %d detections → %d rally clips",
                    before_rally,
                    len(detections),
                )
            except Exception as rally_err:
                logger.warning("Rally grouping failed (%s) — using per-action clips", rally_err)

            # Audio energy weighting — boost detections near loud moments,
            # penalize detections during silence, then drop low-confidence ones
            try:
                from ml.pipeline.audio import weight_detections_by_audio
                detections = weight_detections_by_audio(str(local_video), detections)
                before = len(detections)
                detections = [d for d in detections if d["confidence"] >= 0.40]
                if len(detections) < before:
                    logger.info("Audio filter dropped %d quiet detections", before - len(detections))
            except Exception as audio_err:
                logger.warning("Audio weighting failed (%s) — using unweighted", audio_err)

            # CLIP verification gate — filter out false-positive detections
            from app.config import settings as app_settings
            if app_settings.clip_verify_enabled:
                try:
                    from ml.pipeline.verify import verify_detections
                    before = len(detections)
                    detections = verify_detections(str(local_video), detections)
                    logger.info("CLIP filter: %d → %d detections", before, len(detections))
                except Exception as clip_err:
                    logger.warning("CLIP verification failed (%s) — using unfiltered", clip_err)
            else:
                logger.info("CLIP verification disabled — skipping")

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
                    "start_time": cd["start"],
                    "end_time": cd["end"],
                    "clip_url": clip_url,
                    "thumbnail_url": thumb_url,
                    "labels": cd.get("labels", []),
                })

            sync_save_clips(rows)
            sync_set_game_status(gid, "ready", processed_at=datetime.now(timezone.utc))
            logger.info("Done: %d clips for game %s", len(rows), game_id)

    except Exception as exc:
        logger.exception("Processing failed for game %s", game_id)
        sync_set_game_status(gid, "failed", error_message=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, name="process_dead_time", max_retries=2, default_retry_delay=60)
def process_dead_time_task(self, run_id: str, raw_video_url: str):
    """
    Separate dead-time processing pipeline:
    1. Run dead-time detection locally
    2. Download source + cut dead-time clips with FFmpeg
    3. Upload dead-time clips to R2
    4. Persist dead-time clip rows
    """
    from app.workers._sync_db import sync_set_dead_time_run_status, sync_save_dead_time_clips
    from app.services import storage as s3

    rid = uuid.UUID(run_id)
    r2_key = urlparse(raw_video_url).path.lstrip("/")
    logger.info("Starting dead-time processing for run %s", run_id)

    try:
        sync_set_dead_time_run_status(rid, "processing")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_video = tmp / "game.mp4"
            logger.info("Downloading key=%s from R2 for dead-time processing", r2_key)
            s3.download_file(r2_key, local_video)

            detections = _run_dead_time_detection_local(str(local_video))
            logger.info("Dead-time detections: %d", len(detections))

            from ml.pipeline.clip import generate_clips
            clips_data = generate_clips(str(local_video), detections, tmp)

            rows = []
            for cd in clips_data:
                clip_id = uuid.uuid4()
                clip_url = s3.upload_file(
                    cd["clip_path"],
                    s3.dead_time_clip_key(rid, clip_id),
                    "video/mp4",
                )
                thumb_url = None
                if cd.get("thumb_path"):
                    thumb_url = s3.upload_file(
                        cd["thumb_path"],
                        s3.dead_time_thumbnail_key(rid, clip_id),
                        "image/jpeg",
                    )

                rows.append({
                    "id": clip_id,
                    "run_id": rid,
                    "start_time": cd["start"],
                    "end_time": cd["end"],
                    "score": float(cd.get("score", cd.get("confidence", 0.0))),
                    "clip_url": clip_url,
                    "thumbnail_url": thumb_url,
                })

            sync_save_dead_time_clips(rows)
            sync_set_dead_time_run_status(rid, "ready", processed_at=datetime.now(timezone.utc))
            logger.info("Dead-time done: %d clips for run %s", len(rows), run_id)

    except Exception as exc:
        logger.exception("Dead-time processing failed for run %s", run_id)
        sync_set_dead_time_run_status(rid, "failed", error_message=str(exc))
        raise self.retry(exc=exc)
