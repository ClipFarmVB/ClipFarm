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


def _will_attempt_modal(r2_key: str) -> bool:
    """Whether ball tracking will try Modal GPU for this run.

    Single source of truth for the condition below and in `_track_ball_cached`
    so the progress bar's tracking span can't be mis-sized against the path the
    pipeline actually takes.
    """
    return _modal_configured() and bool(r2_key)


def _track_ball_modal(local_video: Path, r2_key: str, sample_every: int, on_progress=None):
    """
    Run ball tracking on Modal GPU (CF-11): ~27-42 min CPU -> low single-digit
    minutes on a T4. Raises on any failure; caller falls back to local CPU.

    Prefers the streaming function (track_ball_stream) so the progress bar
    moves during the GPU run; falls back to the blocking call when only the
    older deployment exists.
    """
    import os
    import modal as modal_sdk
    from ml.pipeline.ball import TrackedBall, BallPosition
    from app.services import storage as s3

    video_url = s3.presign_url(r2_key, expires_in=3600)
    api_key = os.environ["ROBOFLOW_API_KEY"]

    streaming_started = False
    try:
        fn = modal_sdk.Function.from_name("clipfarm-ball-tracking", "track_ball_stream")
        positions = None
        for event in fn.remote_gen(video_url, api_key, sample_every):
            streaming_started = True
            if "progress" in event and on_progress:
                try:
                    on_progress(event["progress"])
                except Exception:
                    logger.warning("Progress callback failed", exc_info=True)
            elif "positions" in event:
                positions = event["positions"]
        if positions is None:
            raise RuntimeError("Modal stream ended without a positions event")
        return TrackedBall(positions=[BallPosition(**p) for p in positions])
    except modal_sdk.exception.NotFoundError:
        # from_name is lazy, so a missing deploy can surface on the first
        # remote_gen pull — hence the try spans the loop. But once the stream
        # has produced events, a NotFound is a mid-run failure, not a missing
        # function: re-raise so the caller falls back to local CPU instead of
        # silently re-running the whole GPU job on the blocking function.
        if streaming_started:
            raise
        logger.info("track_ball_stream not deployed yet — using blocking Modal call")

    fn = modal_sdk.Function.from_name("clipfarm-ball-tracking", "track_ball_remote")
    positions = fn.remote(video_url, api_key, sample_every)
    return TrackedBall(positions=[BallPosition(**p) for p in positions])


def _ball_cache_key(video_md5: str, sample_every: int) -> str:
    from ml.pipeline.ball import MODEL_ID
    model_slug = MODEL_ID.replace("/", "-")
    return f"ball-cache/{video_md5}-{model_slug}-s{sample_every}.json"


def _track_ball_cached(
    local_video: Path,
    tmp: Path,
    sample_every: int,
    r2_key: str = "",
    video_md5: str | None = None,
    on_progress=None,
):
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
    from ml.pipeline.ball import track_ball, TrackedBall, BallPosition

    cache_key = _ball_cache_key(video_md5 or _file_md5(local_video), sample_every)
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
    if _will_attempt_modal(r2_key):
        try:
            tracker = _track_ball_modal(local_video, r2_key, sample_every, on_progress=on_progress)
            logger.info("Ball tracking ran on Modal GPU: %d positions", len(tracker.positions))
        except Exception as modal_err:
            logger.warning("Modal ball tracking failed (%s) — falling back to local CPU", modal_err)

    if tracker is None:
        tracker = track_ball(
            str(local_video), os.environ["ROBOFLOW_API_KEY"],
            sample_every=sample_every, on_progress=on_progress,
        )

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
        sync_set_game_progress,
        sync_save_clips,
        sync_delete_game_clips,
        sync_game_exists,
        sync_note_game_error,
        sync_set_original_duration,
        sync_settle_upload_charge,
    )
    from app.workers.progress import (
        GameProgress, compute_stage_spans, CONDENSE_SECS_PER_KEPT_SEC,
    )
    from app.workers.locks import GameLock, LockLost, LockNotSessionScoped
    from app.config import settings as _cfg
    from app.services import storage as s3
    import cv2 as _cv2
    import os as _os

    gid = uuid.UUID(game_id)
    r2_key = urlparse(raw_video_url).path.lstrip("/")
    logger.info("Starting processing for game %s", game_id)

    def _abandoned() -> bool:
        """True if the game was deleted, meaning all remaining work is wasted.

        Checked at each stage boundary so a delete landing during a long stage
        (ball tracking can run for minutes) stops the pipeline at the next
        checkpoint instead of running to completion, FK-failing on save, and
        retrying. It can't interrupt a single in-flight stage — only the gaps
        between them (see the Level-2 hard-stop follow-up for true cancellation).
        """
        if sync_game_exists(gid):
            return False
        logger.info("Game %s no longer exists — abandoning processing", game_id)
        return True

    # The game may already be gone: deleted before the worker picked this up, or
    # this is a retry scheduled after a delete. Its DB row and raw video are gone,
    # so there's nothing to do — abandon rather than 404 on download and retry.
    if _abandoned():
        return

    # CF-65a: never process one game on two workers at once. A redelivered or
    # duplicated task whose game is already in flight is a harmless no-op.
    # The lock lives on its own database connection (CF-184), so if this worker
    # is hard-killed the lock dies with it and the requeued copy can run.
    lock = GameLock(gid)

    # acquire() is INSIDE the retryable try on purpose: taking the lock is a
    # database round trip, and a transient Postgres blip while taking it must
    # retry like any other failure. Raising above the try would be a permanent
    # failure instead — acks_late acks a task that raises, so nothing would
    # redeliver it and the game would sit in `queued`.
    def _require_lock() -> None:
        """Abort the job if the lock stopped being ours part-way through.

        The connection can die without this worker dying — a failover, an
        `idle_session_timeout`, a `pg_terminate_backend` — and Postgres releases
        the lock the moment it does. Nothing about *this* process changes, so
        without a check the job runs on to completion while a redelivery at the
        visibility timeout acquires the free lock and runs beside it. Checked at
        the long stage boundaries: it bounds the unprotected stretch to one
        stage rather than the whole job, and the retry re-acquires cleanly.
        """
        if not lock.still_held():
            raise LockLost(
                f"Lost the processing lock for game {gid} mid-job — aborting so "
                "the retry can re-acquire it"
            )

    try:
        try:
            acquired = lock.acquire()
        except LockNotSessionScoped as lock_cfg_err:
            # A misconfigured lock database, not a transient failure: retrying
            # cannot fix it, and burning the retries would leave the game
            # terminally `failed` with its quota already charged. Leave it
            # `queued` — still runnable, once the environment is — and say why
            # loudly enough to reach Sentry.
            logger.exception(
                "Refusing to process game %s: the lock database cannot hold a "
                "session-scoped lock. Game left queued; fix LOCK_DATABASE_URL "
                "and resubmit.", gid,
            )
            # Also written to the row, because a misconfiguration strands EVERY
            # game the same way: without this the backlog exists only as Sentry
            # events, and recovering means guessing which games to resubmit.
            # With it they are one query away (see sync_note_game_error).
            try:
                sync_note_game_error(
                    gid, f"Not started: lock database misconfigured ({lock_cfg_err})"
                )
            except Exception:
                logger.warning("Could not note the lock misconfiguration on game %s", gid)
            return

        if not acquired:
            logger.warning("Game %s already being processed elsewhere — skipping duplicate delivery", gid)
            return

        sync_set_game_status(gid, "processing")
        # Written directly rather than through GameProgress: the stage spans
        # need the downloaded file's MD5 (cache probe below), which isn't
        # available until the download this bar is reporting has finished.
        sync_set_game_progress(gid, 0.0, "downloading")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            local_video = tmp / "game.mp4"
            # Per-stage wall-clock timing. Each _lap(name) logs a greppable
            # "STAGE_TIMING stage=<name> seconds=<n>" line; grep the worker log
            # to profile a run or re-calibrate the progress-bar weights in
            # progress.py. Cheap (one log line per stage) so it stays on always.
            import time as _timing
            _clock = {"t": _timing.perf_counter()}
            _run_t0 = _clock["t"]
            def _lap(_stage):
                _now = _timing.perf_counter()
                logger.info("STAGE_TIMING stage=%s seconds=%.2f", _stage, _now - _clock["t"])
                _clock["t"] = _now
            _clock["t"] = _timing.perf_counter()
            logger.info("Downloading key=%s from R2", r2_key)
            s3.download_file(r2_key, local_video)
            _lap("downloading")

            # Video metadata — used by all stages below
            _cap = _cv2.VideoCapture(str(local_video))
            _fps         = _cap.get(_cv2.CAP_PROP_FPS) or 30.0
            _frames      = int(_cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            _frame_h     = int(_cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
            _cap.release()
            video_duration = _frames / _fps

            # CF-91: the api can only enforce the duration cap against what the
            # client declared — this is the first point the real length is
            # known, and it is still ahead of every expensive stage. Record the
            # measurement, then decide.
            try:
                sync_set_original_duration(gid, video_duration)
            except Exception:
                # Accounting must not break processing — the gate below reads
                # the probed value directly, so it holds either way.
                logger.warning("Failed to record duration for game %s", game_id, exc_info=True)
            if video_duration > _cfg.max_upload_duration_seconds:
                logger.warning(
                    "Game %s is %.0f s, over the %.0f s cap — refusing to process",
                    game_id, video_duration, _cfg.max_upload_duration_seconds,
                )
                sync_set_game_status(
                    gid, "failed",
                    error_message=(
                        f"Video is {video_duration / 60:.0f} min long; the maximum is "
                        f"{_cfg.max_upload_duration_seconds / 60:.0f} min. "
                        "Split it into separate uploads."
                    ),
                )
                # The object is rejected and will never be processed, so it is
                # pure stored cost — nothing else reclaims it (the retention
                # sweep is still a backlog item). Best-effort: a failed delete
                # must not turn a clean rejection into a retried task.
                try:
                    s3.delete_file(r2_key)
                except Exception:
                    logger.warning("Could not delete rejected upload %s", r2_key, exc_info=True)
                # Note what is deliberately NOT done here: the quota charge is
                # left at whatever was reserved at accept time. Settling it up
                # to the real length would bill a rejected upload for footage
                # that never ran — 6 h of probed video against a 6 h daily cap
                # wipes out the day for work we refused and deleted. The slot
                # it consumed is the cost; the count cap is what bounds someone
                # doing this repeatedly. It also keeps an honest client whose
                # container reports a wrong duration from losing their day to a
                # metadata bug.
                return

            # Accepted. Settle the quota charge to the measured length — this
            # is what makes charging an undeclared duration at the per-video
            # maximum fair rather than punitive, and what stops an
            # under-declared one from sticking as the charge.
            try:
                sync_settle_upload_charge(gid, video_duration)
            except Exception:
                logger.warning("Failed to settle quota charge for game %s", game_id, exc_info=True)

            # fps-aware sampling: ~3 ball detections per second of video
            # regardless of source frame rate (tuned at 30fps/every-10th;
            # a 60fps video would otherwise double inference cost).
            sample_every = max(1, round(_fps / 3.0))

            # Progress bar stage budget: a ball-cache hit collapses ~30 min
            # of tracking into seconds, so probe the cache now and give the
            # tracking stage a realistic slice of the bar either way.
            video_md5 = _file_md5(local_video)
            ball_cache_hit = bool(
                _os.environ.get("ROBOFLOW_API_KEY")
                and s3.object_exists(_ball_cache_key(video_md5, sample_every))
            )
            progress = GameProgress(
                compute_stage_spans(
                    condense, ball_cache_hit,
                    modal=_will_attempt_modal(r2_key),
                ),
                write=lambda p, stage: sync_set_game_progress(gid, p, stage),
            )
            _lap("setup")  # metadata probe + md5 + cache probe

            # ── Stage 0: Audio energy envelope ────────────────────────────
            # Extracted once (seconds, even for a full game) and reused by
            # cheer scoring and confidence weighting below.
            progress.stage("analyzing_audio")
            audio_energy = None
            try:
                from ml.pipeline.audio import compute_audio_energy
                audio_energy = compute_audio_energy(str(local_video))
                if audio_energy is None:
                    logger.warning("No usable audio track — cheer scoring disabled")
            except Exception as audio_err:
                logger.warning("Audio extraction failed (%s)", audio_err)
            _lap("analyzing_audio")

            # ── Stage 1: Ball tracking → rally windows ────────────────────
            # Primary pipeline: Roboflow ball model detects every contact,
            # trajectory physics classify the action, contacts are grouped
            # into rally windows. Fast, occlusion-proof, no GPU needed.
            progress.stage("tracking_ball")
            detections: list[dict] = []
            ball_ok = False
            ball_contacts: list[dict] = []
            ball_positions: list[dict] = []
            if _os.environ.get("ROBOFLOW_API_KEY"):
                try:
                    from ml.pipeline.ball import find_contacts, contacts_to_rallies
                    tracker   = _track_ball_cached(
                        local_video, tmp, sample_every=sample_every, r2_key=r2_key,
                        video_md5=video_md5, on_progress=progress.update,
                    )
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
            _lap("tracking_ball")
            _require_lock()   # the longest stage just ran — are we still alone?

            # ── Stage 2: Highlight scoring → drop low scorers ─────────────
            # Cheer reaction after the rally + rally shape features (+ CLIP
            # frames when enabled) → highlight_score. This is the precision
            # gate: only rallies worth watching go on to pose and cutting.
            from app.config import settings as app_settings
            progress.stage("scoring_highlights")
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
            _lap("scoring_highlights")

            # ── Stage 3: Pose within surviving windows — refine labels ────
            # Runs YOLOv8s-pose only inside rallies that passed the highlight
            # gate. Overrides the ball-trajectory label when pose is more
            # confident.
            progress.stage("refining_actions")
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
            _lap("refining_actions")

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
            progress.stage("cutting_clips")
            # Cutting and uploading share this stage's span: ffmpeg cuts are
            # the bulk of it, uploads the tail.
            clips_data = generate_clips(
                str(local_video), detections, tmp,
                on_progress=lambda f: progress.update(f * 0.7),
            )

            # ── 3. Upload clips and thumbnails, save to DB ────────────────
            rows = []
            for upload_idx, cd in enumerate(clips_data):
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
                progress.update(0.7 + 0.3 * (upload_idx + 1) / len(clips_data))
            _lap("cutting_clips")  # incl audio_weighting (~0) + clip uploads

            # The game can be deleted during this long-running job. If it's gone,
            # persisting clips would violate the FK; abandon and purge the clip
            # artifacts we just uploaded so they don't orphan in R2.
            if not sync_game_exists(gid):
                logger.info(
                    "Game %s deleted during processing — discarding %d clips", game_id, len(rows)
                )
                for row in rows:
                    for url in (row.get("clip_url"), row.get("thumbnail_url")):
                        if url:
                            try:
                                s3.delete_file(urlparse(url).path.lstrip("/"))
                            except Exception as del_err:
                                logger.warning("Orphan clip cleanup failed for %s (%s)", url, del_err)
                return

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

            # Nothing may be written for a game we no longer hold: another
            # worker owning the lock is the authority on its clips.
            _require_lock()
            sync_save_clips(rows)

            # ── Stage 5 (opt-in): condensed dead-time-removed video ───────
            # Derived from the pre-gate rally signal, cut + stitched with
            # ffmpeg. Never fatal: highlight clips are already saved, so a
            # condense failure only means the game page shows no condensed
            # video.
            if condense:
                progress.stage("condensing")
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
                        # Re-price the condensing span now that kept-seconds is
                        # known: its cost tracks kept footage, not video length,
                        # so the static weight can be well off (29-43% observed).
                        kept_seconds = sum(end - start for start, end in windows)
                        progress.rebudget_final_stage(
                            "condensing", kept_seconds * CONDENSE_SECS_PER_KEPT_SEC,
                        )
                        condensed_path, condensed_duration = generate_condensed_video(
                            str(local_video), windows, tmp,
                            on_progress=progress.update,
                        )
                        condensed_url = s3.upload_file(
                            condensed_path, s3.condensed_key(gid), "video/mp4",
                        )
                        # Condensing runs after the clip-save checkpoint and takes
                        # minutes of its own, so re-check: sync_set_condensed_result
                        # would no-op on a deleted game and strand this upload in R2.
                        _require_lock()
                        if _abandoned():
                            try:
                                s3.delete_file(s3.condensed_key(gid))
                            except Exception as del_err:
                                logger.warning(
                                    "Orphan condensed-video cleanup failed (%s)", del_err
                                )
                            return
                        sync_set_condensed_result(
                            gid,
                            condensed_video_url=condensed_url,
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
                _lap("condensing")

            sync_set_game_status(gid, "ready", processed_at=datetime.now(timezone.utc))
            logger.info("Done: %d clips for game %s", len(rows), game_id)
            logger.info("STAGE_TIMING stage=TOTAL seconds=%.2f", _timing.perf_counter() - _run_t0)

    except Exception as exc:
        # A game deleted mid-flight is the cause of the failure (missing video on
        # retry, FK violation on save), not a transient error — abandon, don't
        # retry. This is the safety net for a deletion at any un-checked point.
        if not sync_game_exists(gid):
            # Keep the traceback: the deletion is the *likely* cause, but a genuine
            # unrelated bug that happens to coincide with one would otherwise vanish
            # without a trace. Info, not error — an abandoned job isn't a failure.
            logger.info(
                "Game %s no longer exists — abandoning (no retry)", game_id, exc_info=True
            )
            return
        logger.exception("Processing failed for game %s", game_id)
        sync_set_game_status(gid, "failed", error_message=str(exc))
        raise self.retry(exc=exc)
    finally:
        lock.release()
