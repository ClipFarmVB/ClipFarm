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


def _track_ball_cached(local_video: Path, tmp: Path, sample_every: int):
    """
    Ball tracking with an R2-backed cache keyed by video content hash.

    Tracking a 22-min video takes ~30 min on CPU; the positions only depend
    on the video bytes, the model version, and the sample rate — so re-runs
    of the same footage (re-uploads, pipeline tuning) load cached positions
    in seconds instead. Cache failures fall through to normal tracking.
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
        tracker = TrackedBall(positions=[BallPosition(**p) for p in data["positions"]])
        logger.info("Ball cache hit (%s): %d positions", cache_key, len(tracker.positions))
        return tracker
    except Exception:
        logger.info("Ball cache miss (%s) — running tracking", cache_key)

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
    1. Download video, extract audio energy envelope (cheap, used by 3 stages)
    2. Ball tracking → contacts with trajectory-based action labels → rally
       windows with shape features (contact count, speed, floor bounce, ...)
    3. Highlight scoring: post-rally cheer + rally shape (+ CLIP frames when
       enabled) → highlight_score per rally; low scorers dropped here
    4. Pose classification — only on rallies that survived scoring
    5. Audio confidence weighting, generate + upload clips, persist to DB

    Falls back to pose-first pipeline if ROBOFLOW_API_KEY is not set.
    """
    from app.workers._sync_db import sync_set_game_status, sync_save_clips
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
            if _os.environ.get("ROBOFLOW_API_KEY"):
                try:
                    from ml.pipeline.ball import find_contacts, contacts_to_rallies
                    # fps-aware sampling: ~3 ball detections per second of video
                    # regardless of source frame rate (tuned at 30fps/every-10th;
                    # a 60fps video would otherwise double inference cost).
                    sample_every = max(1, round(_fps / 3.0))
                    tracker   = _track_ball_cached(local_video, tmp, sample_every=sample_every)
                    contacts  = find_contacts(tracker, frame_height=_frame_h)
                    detections = contacts_to_rallies(contacts, video_duration, _frame_h)
                    ball_ok   = True
                    logger.info("Ball pipeline: %d contacts → %d rallies", len(contacts), len(detections))
                except Exception as ball_err:
                    logger.warning("Ball pipeline failed (%s) — falling back to pose-first", ball_err)

            if not ball_ok:
                # Fallback: pose-first pipeline (Modal GPU or local CPU)
                try:
                    logger.info("Running detection via Modal GPU...")
                    detections = _run_detection_modal(r2_key)
                    logger.info("Modal returned %d detections", len(detections))
                except Exception as modal_err:
                    logger.warning("Modal failed (%s), running local pose detection", modal_err)
                    detections = _run_detection_local(str(local_video))

                try:
                    from ml.pipeline.detect import group_into_rallies
                    before = len(detections)
                    detections = group_into_rallies(detections, video_duration)
                    logger.info("Rally grouping: %d → %d clips", before, len(detections))
                except Exception as rally_err:
                    logger.warning("Rally grouping failed (%s)", rally_err)

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
