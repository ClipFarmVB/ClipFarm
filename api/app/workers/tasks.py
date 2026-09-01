"""Celery tasks for async video processing."""
import hashlib
import json
import logging
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from celery.exceptions import Retry

from app.workers.celery_app import celery_app

if TYPE_CHECKING:
    # Type-only: importing app.config at module scope would run Settings() as a
    # side effect of importing this module. Annotating `settings` at all is the
    # point — untyped, mypy could not see the eight condense_guard_* reads
    # below, so a renamed setting became an AttributeError caught by the
    # builder's own fallback and the worker ran "rules" forever behind one
    # warning per game. That is the failure condense_mode's Literal closes at
    # the front door; this closes it at the back.
    from app.config import Settings

logger = logging.getLogger(__name__)

# Key prefix for game source videos (see storage.game_raw_key). Nothing else
# lives under it — the ball-position cache has its own prefix — so the
# retention sweep can treat every object here as a raw upload.
RAW_PREFIX = "raw/"

# Condense below this much trimming is not worth the re-encode — see the
# condense stage in process_game_task.
CONDENSE_MIN_TRIM_FRACTION = 0.02


def _worth_condensing(kept_seconds: float, video_duration: float) -> bool:
    """
    Whether a keep-window set trims enough to be worth the re-encode.

    The guarded builder abstains by returning one whole-video window, and
    encoding that would spend minutes to upload a copy of the original. Callers
    treat False as "ship the game without a condensed cut", which is also the
    right answer for any other near-total keep.
    """
    if video_duration <= 0:
        return False
    return kept_seconds < video_duration * (1.0 - CONDENSE_MIN_TRIM_FRACTION)


def _clear_previous_condensed(gid) -> None:
    """Drop a previous run's condensed cut: the row first, then the object.

    Both halves matter. Leaving the row would serve a condensed video built from
    a decision this run did not make; leaving the object orphans it, because
    `delete_game` derives its delete list from `condensed_video_url`
    (routers/games.py), so a NULL column makes that MP4 unreachable and it bills
    forever.

    The order is the safe one. A failure between the two leaves an orphan, which
    is logged and costs storage; the reverse order would leave the row pointing
    at a deleted object and 404 the player.

    Only call this for a run that actually judged the video — see
    `_condense_verdict_is_confident`. This deletes user-visible output and
    cannot be undone.

    Raises whatever the row write raises — the caller decides how loud that is.
    """
    from app.services import storage as s3
    from app.workers._sync_db import sync_clear_condensed_result

    previous = sync_clear_condensed_result(gid)
    if not previous:
        # Nothing was there. Skipping the delete keeps the warning below
        # meaningful: it should mean "an object was left behind", not "we asked
        # R2 about a key that has never existed".
        return
    try:
        s3.delete_file(s3.condensed_key(gid))
    except Exception as del_err:
        logger.warning("Orphan condensed-video cleanup failed (%s)", del_err)


def _maybe_clear_previous_condensed(
    gid, windows, *, ball_ok: bool, built_by: str, _require_lock,
) -> bool:
    """Clear a previous condensed cut, but only if this run earned the right to.

    The decision and the deletion are joined here rather than at the call site
    on purpose. The original defect was an *unconditional* clear — the predicate
    was never wrong, it simply was not consulted — so a test that only exercises
    `_condense_verdict_is_confident` leaves the regression that actually
    happened uncovered. With the branch in a function, a test can assert that a
    degraded run performs no deletion at all.

    The lock-checkpoint callable is named `_require_lock`, underscore and all,
    so that `test_every_require_lock_checkpoint_routes_to_a_locklost_handler`
    still sees this checkpoint. That scan finds calls by name; moving one behind
    a parameter would hide it from the routing check while leaving the call
    itself in place — silent under-coverage of the lock invariant, which is the
    failure that test exists to prevent.

    Returns whether the clear was attempted. Never raises except on LockLost,
    which the stage handles: a reporting write must not break processing.
    """
    from app.workers.locks import LockLost

    if not _condense_verdict_is_confident(windows, ball_ok=ball_ok):
        logger.info(
            "Keeping any previous condensed cut: this run did not judge the "
            "video (ball signal: %s, built by %r)",
            "ok" if ball_ok else "missing", built_by,
        )
        return False

    try:
        _require_lock()
        _clear_previous_condensed(gid)
    except LockLost:
        raise
    except Exception as clear_err:
        logger.warning("Could not clear a previous condensed cut (%s)", clear_err)
    return True


def _condense_verdict_is_confident(windows, *, ball_ok: bool) -> bool:
    """Whether this run is entitled to delete a previous run's condensed cut.

    Clearing is right when the pipeline looked at the video and concluded there
    is nothing worth cutting: the old cut then contradicts a real judgement.
    It is wrong when the run could not form that judgement, because the previous
    cut may be the only good one and clearing it destroys the object too.

    Two ways a run reaches "no cut" without judging anything:

    - **No ball signal** (`ball_ok` False). Tracking failed — a Roboflow outage,
      a bad upload — and the stage falls back to pose-derived rallies. That is a
      degraded input, not a verdict about the game.
    - **An abstain.** The guarded builder says so itself: the track is too
      sparse to judge. `Abstained` exists precisely so this is not guesswork.

    "Could not see the ball" and "looked and found nothing to cut" must not both
    delete.
    """
    from ml.pipeline.dead_time import Abstained

    return ball_ok and not isinstance(windows, Abstained)


def _build_condense_windows(
    mode: str,
    contacts: list[dict],
    positions: list[dict],
    duration: float,
    frame_height: int,
    settings: "Settings",
) -> tuple[list[tuple[float, float]], str]:
    """
    Keep-windows for the condense stage, with the name of the builder that
    actually produced them (for logging — the requested mode may not be the one
    that ran).

    Anything that goes wrong *inside* a non-default builder — feature drift, a
    missing frame height, an unknown mode name — falls back to "rules" with a
    warning, because a degraded condense beats a failed run. The scope is worth
    being precise about: both builders come from the same module import below, so
    this fallback covers call-time failures only. If that import itself fails,
    "rules" is just as gone and process_game_task's stage-level `except Exception
    as condense_err` skips condensing altogether — the game still ships `ready`,
    without a condensed cut. config.condense_mode is a Literal, so an unknown mode name can no longer
    arrive from settings; the branch stays for direct callers.

    The None sentinel is load-bearing, not style: active_windows_guarded returns
    [] on a dense track where every contact failed the speed gate and nothing
    anchored a window, which is a real verdict of "no play here". Reading that as
    a failure would rebuild the windows from the very contacts the gate rejected
    — the junk the mode exists to remove. Only an exception means "fall back".
    """
    from ml.pipeline.dead_time import (
        active_windows_from_contacts,
        active_windows_guarded,
        bridge_windows_by_motion,
    )

    windows: list[tuple[float, float]] | None = None
    built_by = mode
    if mode != "rules":
        try:
            if mode == "guarded":
                windows = active_windows_guarded(
                    contacts, positions, duration, frame_height,
                    gap_seconds=settings.condense_gap_seconds,
                    min_contacts=settings.condense_min_contacts,
                    pad_before=settings.condense_guard_pad_before,
                    pad_after=settings.condense_guard_pad_after,
                    merge_gap_seconds=settings.condense_guard_merge_gap_seconds,
                    gate_speed=settings.condense_guard_gate_speed,
                    anchor_speed=settings.condense_guard_anchor_speed,
                    min_track_rate=settings.condense_guard_min_track_rate,
                )
            else:
                raise ValueError(f"unknown condense_mode {mode!r}")
        except Exception as mode_err:
            logger.warning(
                "Condense mode %r failed (%s) — using rule-based windows",
                mode, mode_err,
            )
            windows = None
            built_by = "rules"

    if windows is None:
        built_by = "rules"
        windows = active_windows_from_contacts(
            contacts, duration,
            gap_seconds=settings.condense_gap_seconds,
            pad_before=settings.condense_pad_before,
            pad_after=settings.condense_pad_after,
            min_contacts=settings.condense_min_contacts,
            merge_gap_seconds=settings.condense_merge_gap_seconds,
        )
        windows = bridge_windows_by_motion(
            windows, positions,
            speed_pxps=settings.condense_bridge_speed_pxps,
            fast_fraction=settings.condense_bridge_fast_fraction,
            max_bridge_seconds=settings.condense_bridge_max_seconds,
        )
    return windows, built_by



def _file_md5(path: Path) -> str:
    """Content hash of a file (streamed, constant memory)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class PermanentPipelineError(Exception):
    """A failure that retrying cannot fix.

    Celery retries this task twice by default, which is right for transient
    trouble (a flaky download, a Modal blip) and pure waste for a condition that
    is identical on every attempt — e.g. no pose runtime in the image. The
    pose-first scan is the most expensive path in the pipeline, so re-running it
    three times to reach the same conclusion costs real money and queue time.
    """


def _modal_configured() -> bool:
    from app.config import settings
    return bool(settings.modal_token_id and settings.modal_token_secret)


def _will_attempt_modal(r2_key: str) -> bool:
    """Whether the GPU stages will try Modal for this run.

    Covers ball tracking (CF-11) and pose (CF-164) alike: both need a presigned
    URL for the raw object, so both are gated on the same two facts. Single
    source of truth for the call sites below so the progress bar's tracking span
    can't be mis-sized against the path the pipeline actually takes.
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
    where a local runtime exists.

    In the deployed image it does not (CF-164 took the ML stack out; CF-225
    took the model-cache disk that fed it), so there the local step only
    re-raises. When Modal was tried first, what it raises names BOTH failures,
    chains from the Modal one — that is the half an operator can act on — and
    keeps the local failure's type. A missing-`inference` traceback on its own
    reads as a packaging bug and hides the outage that caused it.
    """
    import os
    from app.services import storage as s3
    from ml.pipeline.ball import (
        BallRuntimeUnavailable, track_ball, TrackedBall, BallPosition,
    )

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
    modal_err: Exception | None = None
    if _will_attempt_modal(r2_key):
        try:
            tracker = _track_ball_modal(local_video, r2_key, sample_every, on_progress=on_progress)
            logger.info("Ball tracking ran on Modal GPU: %d positions", len(tracker.positions))
        except Exception as err:
            modal_err = err
            logger.warning("Modal ball tracking failed (%s) — falling back to local CPU", err)

    if tracker is None:
        # Read outside the try: a missing key is a configuration error, and
        # wrapped below it would surface as "…and locally ('ROBOFLOW_API_KEY')",
        # a KeyError dressed up as a tracking failure. process_game guards on
        # this already; the eval and diagnose paths call straight in here.
        api_key = os.environ["ROBOFLOW_API_KEY"]
        try:
            tracker = track_ball(
                str(local_video), api_key,
                sample_every=sample_every, on_progress=on_progress,
            )
        except Exception as local_err:
            if modal_err is None:
                raise
            # Both paths failed and only one of them is the interesting one. The
            # local attempt was the fallback, so raising *its* error alone —
            # usually a missing runtime, on an image that is meant not to have
            # one — describes the fallback rather than the failure. Every local
            # failure is wrapped, not just the missing runtime, so the Modal
            # cause never depends on which one came back.
            #
            # The TYPE still follows the local failure. "No runtime here" is
            # true whether or not Modal was tried first, and flattening it to
            # RuntimeError erased that in the only environment where the
            # condition is expected — a deployed run, which always tries Modal.
            # Nothing translates it to PermanentPipelineError (see its
            # docstring), so it cannot suppress a retry over a Modal blip.
            message = f"Ball tracking failed on Modal ({modal_err}) and locally ({local_err})"
            if isinstance(local_err, BallRuntimeUnavailable):
                raise BallRuntimeUnavailable(message) from modal_err
            raise RuntimeError(message) from modal_err

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
    from app.config import settings as app_settings
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

            clip_path, thumb_path = recut_single(
                str(local_video), start, end, tmp,
                threads=app_settings.ffmpeg_threads,
            )

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


def _json_safe_key(key):
    """`_json_safe` for a dict key: hashable in, hashable out.

    An ndarray is unhashable and so can never be a key; a tuple key can, and
    stays a tuple rather than becoming the list `_json_safe` would return.
    """
    if isinstance(key, tuple):
        return tuple(_json_safe_key(k) for k in key)
    if hasattr(key, "dtype") and hasattr(key, "tolist"):
        return key.tolist()
    return key


def _json_safe(value):
    """Plain-Python copy of a nested structure, with numpy scalars unwrapped.

    The pipeline builds confidences with numpy arithmetic and the results leak
    out as numpy scalars: `ball.py::classify_contact_action` computes
    `min(0.88, 0.65 + sp_after / 1500.0)` where `sp_after` is an `np.hypot`
    result, so the numpy operand can win the `min()`. Speed alone does not
    decide it: the expression sits inside `y_frac < 0.45 and vy_after > 60.0
    and sp_after > 180.0` (`ball.py:394`), and above ~345 px/s the constant
    0.88 wins instead — so ~180-345 px/s is necessary for the leak, not
    sufficient. When it does leak, `round()` preserves the type and it lands in
    the rally's "confidence" via `_make_rally`.

    In one process that is invisible. Across the Modal boundary it is a live
    hazard: a numpy-2 pickle names `numpy._core`, which a numpy-1.x image cannot
    import, so the call fails at *deserialization* and the caller sees only
    "Modal failed — falling back to local CPU". With no ultralytics in this
    image, pose then silently never runs: green deploy, one warning line,
    trajectory-only labels. Worse, it is data-dependent — a fast enough contact
    yields the plain 0.88 literal and the same code path works.

    Matching pins fix it today (see Dockerfile.api); sending plain floats means a
    future bump on either side cannot bring the *request* back. The response is
    not covered by this function and needs no equivalent pass today —
    `classify_action` returns float literals and `x()`/`y()` cast with `float()`,
    so what comes back is already plain — but that is a property of those
    heuristics, not a guarantee this function makes. Anything that starts
    returning numpy from the Modal side needs the same treatment there.
    """
    if isinstance(value, dict):
        # Keys ride the same pickle as values, so a numpy scalar key is the
        # same hazard — but they cannot go through `_json_safe` itself, which
        # returns lists for sequences and would make a tuple key unhashable.
        return {_json_safe_key(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # numpy scalars and arrays, without importing numpy into this module.
    # 0-d .tolist() yields a Python scalar; nd yields nested lists.
    if hasattr(value, "dtype") and hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return value


def _classify_windows_modal(r2_key: str, windows: list[dict]) -> list[dict]:
    """
    Pose-refine rally windows on Modal GPU (CF-164). Raises on any failure;
    the caller falls back to local CPU pose.

    Blocking, unlike `_track_ball_modal`: refinement visits only the rallies
    that survived the highlight gate, so there is far less wall-clock to report
    against, and `classify_within_windows` has no progress callback to bridge.
    """
    import modal as modal_sdk
    from app.config import settings
    from app.services import storage as s3

    video_url = s3.presign_url(r2_key, expires_in=3600)
    fn = modal_sdk.Function.from_name("clipfarm-pose", "classify_windows_remote")
    return fn.remote(
        video_url, _json_safe(windows),
        settings.pose_model, settings.pose_imgsz, settings.pose_skip_frames,
    )


def _refine_with_pose(
    local_video: Path, r2_key: str, windows: list[dict], game_id: str = "",
) -> list[dict]:
    """
    Stage 3 pose refinement: Modal GPU when configured, local CPU otherwise.

    Same degradation contract as ball tracking — a Modal outage costs speed and
    label quality, never the run. Note the deployed worker image no longer
    carries torch (CF-164), so the local branch is a real path only for
    development installs; without ultralytics `classify_within_windows`
    no-ops and the ball-trajectory labels stand.
    """
    from app.config import settings

    # The highlight gate can drop every rally. `classify_within_windows` already
    # no-ops on an empty list locally; without this the Modal path would cold-start
    # a T4, open the video and hand back [].
    if not windows:
        return windows

    if _will_attempt_modal(r2_key):
        try:
            refined = _classify_windows_modal(r2_key, windows)
            # Deliberately "returned", not "refined": this side only sees the
            # list come back, and its length is just the input size. How many
            # windows pose actually scored is logged by classify_within_windows
            # on the Modal side.
            logger.info("Pose refinement returned %d windows from Modal GPU", len(refined))
            return refined
        except Exception as modal_err:
            logger.warning(
                "Modal pose refinement failed (%s) — falling back to local CPU", modal_err
            )

    # Imported here, not at the top: on the Modal path this pulls cv2 + numpy
    # (and, inside classify_within_windows, torch) for nothing.
    from ml.pipeline.detect import classify_within_windows, pose_available

    # Reaching here means Modal was unconfigured or failed. In the deployed image
    # there is no pose runtime behind it (CF-164), so `classify_within_windows`
    # would quietly return the windows untouched and the game would keep
    # trajectory-only labels — WORSE than the pre-CF-164 baseline, which always
    # had local pose to fall back on.
    #
    # Don't let that pass as a warning line in a stream of them. This is logged at
    # ERROR, which the Sentry logging integration promotes to an event, and
    # carries the game id so degraded games can be listed and re-run once Modal is
    # healthy. Not fatal: clips and their boundaries come from ball tracking and
    # are unaffected — only the action labels are.
    if not pose_available():
        logger.error(
            "POSE_DEGRADED game=%s windows=%d — no GPU and no local pose runtime; "
            "keeping trajectory-only labels. Re-run this game once Modal is healthy.",
            game_id or "unknown", len(windows),
        )
        return windows

    return classify_within_windows(
        str(local_video), windows,
        model_name=settings.pose_model,
        imgsz=settings.pose_imgsz,
        skip_frames=settings.pose_skip_frames,
    )


def _run_detection(video_path: str, r2_key: str = "") -> list[dict]:
    """
    Pose-first full-video scan — the fallback when the ball pipeline is out.

    Runs on Modal GPU when configured. This scans every skip_frames-th frame of
    the entire video (not just rally windows), so it is the most expensive thing
    the pipeline can do on CPU and the one that most wants a GPU.
    """
    from app.config import settings

    if _will_attempt_modal(r2_key):
        try:
            import modal as modal_sdk
            from app.services import storage as s3

            fn = modal_sdk.Function.from_name("clipfarm-pose", "detect_actions_remote")
            detections = fn.remote(
                s3.presign_url(r2_key, expires_in=3600),
                settings.pose_model, settings.pose_imgsz, settings.pose_skip_frames,
            )
            logger.info("Pose-first scan ran on Modal GPU: %d detections", len(detections))
            return detections
        except Exception as modal_err:
            logger.warning(
                "Modal pose-first scan failed (%s) — falling back to local CPU", modal_err
            )

    # allow_stub is the whole safety question here. `run_detection` degrades to
    # `_stub_detections` when the pose import fails — up to 15 fabricated clips at
    # invented timestamps, flat 0.75 confidence — and this pipeline cuts, uploads
    # and persists whatever it returns. Harmless while the image always carried
    # ultralytics; since CF-164 it does not, so that stub sits one Modal failure
    # away from production data.
    #
    # Gated on its own setting, not on `debug`: `debug` is a general dev-fallbacks
    # flag living in the shared api+worker env group, so turning it on to debug the
    # api would quietly grant this permission too. Off by default, and
    # `run_detection` raises instead — the caller marks the game `failed` with that
    # message and reports to Sentry, which is the right outcome, since no ball
    # pipeline, no GPU and no pose runtime means there is nothing to detect.
    from ml.pipeline.detect import PoseRuntimeUnavailable, run_detection

    try:
        return run_detection(
            video_path,
            model_name=settings.pose_model,
            imgsz=settings.pose_imgsz,
            skip_frames=settings.pose_skip_frames,
            allow_stub=settings.allow_stub_detections,
        )
    except PoseRuntimeUnavailable as exc:
        # Translated at the boundary so the task handler can recognise it without
        # importing ml (and without matching on a message).
        raise PermanentPipelineError(str(exc)) from exc


def _pose_first_fallback(
    video_path: str, r2_key: str, ball_err: str | None
) -> list[dict]:
    """The pose-first scan, reporting the ball failure that sent us here.

    Split out of `process_game` for one reason: it is the only place both halves
    of a two-stage failure are in scope, and inline there it was unreachable by
    a test (there is no seam into `process_game_task` — see
    test_worker_safety.py).

    `ball_err` is the formatted message, not the exception: holding the
    exception kept its traceback — and through it the failed stage's frame
    locals, which for ball tracking means a model handle and cv2/numpy buffers
    — alive for the rest of the run. Worth avoiding for a value only ever used
    as text: it was written against the 512 MB worker (CF-164), and while
    CF-240 has since bought real headroom, cutting still runs libx264 next to
    it.

    When both stages are down it is usually one event — no Modal — but only the
    pose message ("Pose detection is unavailable…") reaches the game's
    `error_message` and Sentry, and it says nothing about the stage that failed
    first. So every failure out of the scan is re-raised carrying `ball_err`.

    Every failure, not just `PermanentPipelineError`: the scan also dies on
    `RuntimeError("Cannot open video: …")`, a decode error mid-run, or an OOM,
    and those dropped `ball_err` exactly like the case this guard was written
    for. The re-raise preserves permanence — a permanent failure stays
    permanent, so the task handler skips the retry once it has recorded the
    failure — and nothing else about the type, which no caller reads. (The
    handler's gate is `reported and isinstance(...)`: if both status writes also
    fail, it retries even a permanent failure, to get the status written.)
    """
    try:
        return _run_detection(video_path, r2_key)
    except Exception as pose_err:
        if ball_err is None:
            raise
        # Separated: `pose_err` is an arbitrary exception's str() and need not
        # end in punctuation, so the two halves ran together into one sentence.
        message = f"{pose_err} — ball tracking failed first: {ball_err}"
        if isinstance(pose_err, PermanentPipelineError):
            raise PermanentPipelineError(message) from pose_err
        raise RuntimeError(message) from pose_err


def _sweep_expired_raw_uploads() -> None:
    """
    Delete raw uploads older than `raw_upload_retention_days` (CF-194).

    Two passes, and the second is what makes this safe to run repeatedly:

    1. **Rows.** Every game past the cutoff has its `raw_video_url` cleared —
       guarded on the URL the sweep saw, so a row that changed underneath is
       left alone. Nothing is deleted from storage in this pass.
    2. **Objects.** Every object under `raw/` older than the cutoff that no game
       row still references is deleted, in batches. That covers the keys pass 1
       just released *and* any object orphaned by an earlier failed delete or a
       worker killed between the two passes, so the sweep converges instead of
       leaking: null-first ordering can only ever cost storage, and this is what
       reclaims it. Two independent guards protect a live upload — it is
       referenced by its row (`uploading` rows included, from the moment the
       ticket is issued), and it is newer than the cutoff.

    Order matters between the passes. Deleting first and clearing second can
    leave a row pointing at a missing object, which turns every later trim into
    a download failure plus two Celery retries — a broken feature rather than a
    storage cost.

    Purging a raw video is not free: `recut_clip` re-cuts from it, so trimming a
    clip stops working once its game's source expires. That is the deliberate
    trade (the retention window is how long trims stay available) — the API
    reports it via ClipOut.source_available so the UI can say so.

    **Where this runs.** At the end of a successful process_game run: no cron or
    celery-beat exists in this stack, and the worker is the one place already
    off the request path. `_sweep_abandoned_uploads` (CF-163) makes the other
    choice — the owner's next presign — but that is per-user and only reaches
    people who are still uploading. Retention is a global cost, so it wants a
    global trigger. Both share the same limitation: enforcement stops while the
    system is idle, which is also when the bill stops growing. A beat schedule
    is the real fix and is a follow-up, not this card.

    Reporting must never break processing: every failure is logged and
    swallowed, and a partial sweep resumes on the next run.
    """
    from app.config import settings as _cfg
    from app.services import storage as s3
    from app.workers._sync_db import (
        sync_clear_raw_video_url,
        sync_expired_raw_uploads,
        sync_referenced_raw_keys,
    )

    days = _cfg.raw_upload_retention_days
    if days <= 0:  # 0 = keep forever
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Pass 1: release expired rows ─────────────────────────────────────────
    try:
        expired = sync_expired_raw_uploads(cutoff)
    except Exception as exc:
        logger.warning("Raw-upload retention: could not list expired rows (%s)", exc)
        return

    released = 0
    for expired_gid, raw_url in expired:
        try:
            if sync_clear_raw_video_url(expired_gid, raw_url):
                released += 1
        except Exception as exc:
            logger.warning(
                "Raw-upload retention: could not release game %s (%s)", expired_gid, exc
            )

    # ── Pass 2: reclaim unreferenced objects ─────────────────────────────────
    # The reference set is the guard, so a failure to build it aborts the pass.
    # Deleting against a partial set could destroy a live upload.
    try:
        referenced = sync_referenced_raw_keys()
    except Exception as exc:
        logger.warning(
            "Raw-upload retention: released %d row(s), but could not read the "
            "reference set — leaving objects for the next sweep (%s)", released, exc,
        )
        return

    try:
        stale = [
            obj["key"]
            for obj in s3.list_objects(RAW_PREFIX)
            if obj["last_modified"] < cutoff and obj["key"] not in referenced
        ]
    except Exception as exc:
        logger.warning("Raw-upload retention: could not list %s (%s)", RAW_PREFIX, exc)
        return

    if not stale:
        return

    failed = s3.delete_files(stale)
    if failed:
        # Not lost: they stay unreferenced and past the cutoff, so the next
        # sweep finds them again.
        logger.warning(
            "Raw-upload retention: %d object(s) could not be deleted, retrying next sweep",
            len(failed),
        )
    logger.info(
        "Raw-upload retention: released %d row(s), reclaimed %d object(s) older than %d day(s)",
        released, len(stale) - len(failed), days,
    )


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
    #
    # Guarded because this is the FIRST database call on a retry, and it sits
    # above the `try` below — so an exception here is uncaught, and there is no
    # `autoretry_for` on this task to schedule another attempt after it. On a
    # retry the handler scheduled for a database outage, an unguarded probe here
    # would spend that attempt and end the chain, which is the failure CF-277 is
    # about, one level up.
    #
    # A probe that cannot answer proceeds rather than abandons: the run may fail
    # later, and the handler's own probe catches a genuine deletion. Abandoning
    # on an unanswerable probe would silently drop a game that still exists.
    try:
        if _abandoned():
            return
    except Exception:
        logger.warning(
            "Could not check whether game %s still exists — proceeding; a real "
            "deletion is caught at the next checkpoint", game_id, exc_info=True,
        )

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

    def _mark_failed_if_lock_free(message: str) -> None:
        """Write `failed` for this game, but only if nobody else holds its lock.

        Used after we have lost the lock and spent our retries: the row must not
        strand in `processing`, but it also must not be stamped over a live run
        another worker owns. Taking the lock proves the game is not in flight
        anywhere — and the write happens while the probe is still held, so the
        fact it establishes cannot lapse between the check and the write. A probe
        that cannot answer leaves the row alone: a wrong `failed` on a live job
        is worse than a row left behind for the next run.
        """
        probe = GameLock(gid)
        try:
            if not probe.acquire():
                return
        except Exception:
            logger.warning(
                "Could not probe the lock for game %s — leaving its row alone",
                game_id, exc_info=True,
            )
            return
        try:
            sync_set_game_status(gid, "failed", error_message=message)
        except Exception:
            # Same class as the handler below, milder: retries are already spent
            # here so no retry decision is lost, and the row strands either way
            # because the write is what failed. What this saves is the caller's
            # `raise` — without it the DB error replaces the LockLost in what
            # Sentry sees, and the reason the game was abandoned disappears.
            logger.exception(
                "Could not mark game %s failed after losing its lock", game_id
            )
        finally:
            probe.release()

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
                # pure stored cost, and the retention sweep would not reclaim
                # it for another `raw_upload_retention_days`. Best-effort: a
                # failed delete
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
            ball_err: str | None = None
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
                except Exception as err:
                    # The formatted message, not the exception — see
                    # _pose_first_fallback: retaining it pins the failed stage's
                    # traceback and frame locals for the rest of the run.
                    ball_err = f"{type(err).__name__}: {err}"
                    # exc_info: this is the first domino, and the pose-first scan
                    # that follows usually fails too on the same outage. Without
                    # the traceback the only record of *why* ball tracking went
                    # is one formatted line.
                    logger.warning(
                        "Ball pipeline failed (%s) — falling back to pose-first",
                        err, exc_info=True,
                    )

            if not ball_ok:
                # Fallback: pose-first full-video scan (ball tracking failed
                # entirely or ROBOFLOW_API_KEY unset — rare path). On Modal GPU
                # when configured; the worker image has no torch to do it here.
                detections = _pose_first_fallback(str(local_video), r2_key, ball_err)

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
            # gate, on Modal GPU when configured (CF-164). Overrides the
            # ball-trajectory label when pose is more confident.
            progress.stage("refining_actions")
            try:
                detections = _refine_with_pose(local_video, r2_key, detections, game_id)
                logger.info("Pose refinement stage done (%d windows)", len(detections))
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
                threads=app_settings.ffmpeg_threads,
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
                        Abstained,
                        active_windows_from_detections,
                    )
                    from ml.pipeline.clip import generate_condensed_video

                    if ball_ok:
                        windows, built_by = _build_condense_windows(
                            app_settings.condense_mode,
                            ball_contacts, ball_positions, video_duration, _frame_h,
                            app_settings,
                        )
                    else:
                        built_by = "pose-fallback"
                        windows = active_windows_from_detections(
                            pre_gate_rallies, video_duration,
                            pad_before=app_settings.condense_pad_before,
                            pad_after=app_settings.condense_pad_after,
                            merge_gap_seconds=app_settings.condense_merge_gap_seconds,
                        )

                    kept_seconds = sum(end - start for start, end in windows)
                    # A condense that keeps ~everything is a no-op, and the
                    # guarded builder abstains by returning exactly that (one
                    # whole-video window) when the ball track is too sparse to
                    # judge. Encoding it would spend minutes to upload a copy of
                    # the original, so skip it: the game still goes ready, just
                    # without a condensed cut.
                    trims_something = _worth_condensing(kept_seconds, video_duration)
                    if windows and not trims_something:
                        # Two different outcomes reach this line and only one is
                        # about trim size, so say which: the builder declining
                        # to judge a sparse track is not "there was little to
                        # cut". `Abstained` carries that from the builder rather
                        # than being inferred back from the window shape.
                        if isinstance(windows, Abstained):
                            logger.info(
                                "Condense abstained (built by %r) — ball track too "
                                "sparse to judge; shipping the full video",
                                built_by,
                            )
                        else:
                            logger.info(
                                "Condense skipped: keep-windows cover %.0fs of %.0fs "
                                "(built by %r) — nothing meaningful to trim",
                                kept_seconds, video_duration, built_by,
                            )

                    if windows and trims_something:
                        # Re-price the condensing span now that kept-seconds is
                        # known: its cost tracks kept footage, not video length,
                        # so the static weight can be well off (29-43% observed).
                        progress.rebudget_final_stage(
                            "condensing", kept_seconds * CONDENSE_SECS_PER_KEPT_SEC,
                        )
                        condensed_path, condensed_duration = generate_condensed_video(
                            str(local_video), windows, tmp,
                            on_progress=progress.update,
                            threads=app_settings.ffmpeg_threads,
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
                    elif not windows:
                        # Not a warning: for the guarded builder [] is a verdict
                        # ("nothing here is play"), which _build_condense_windows
                        # deliberately passes through rather than treating as a
                        # failure. Logging it as a fault contradicts that and
                        # trains readers to ignore the line.
                        logger.info(
                            "Condense produced no keep-windows (built by %r) — "
                            "shipping the game without a condensed cut", built_by,
                        )

                    if not (windows and trims_something):
                        # No cut was produced this run. Clear any previous one —
                        # but only if this run actually judged the video, since
                        # clearing deletes the object too and a degraded re-run
                        # would otherwise destroy the last good cut it cannot
                        # rebuild. Wrapped because a reporting write must never
                        # break the stage.
                        _maybe_clear_previous_condensed(
                            gid, windows,
                            ball_ok=ball_ok, built_by=built_by,
                            _require_lock=_require_lock,
                        )
                except LockLost:
                    # A lost lock is not "condense failed, carry on" — carrying
                    # on reaches the `ready` write below and stamps it over
                    # another worker's live `processing`. `LockLost` subclasses
                    # RuntimeError, so without this it would be swallowed here.
                    # Re-raise past this handler to the task-level one, which
                    # steps aside instead.
                    raise
                except Exception as condense_err:
                    logger.exception("Condense stage failed (%s) — game proceeds without it", condense_err)
                _lap("condensing")

            sync_set_game_status(gid, "ready", processed_at=datetime.now(timezone.utc))
            logger.info("Done: %d clips for game %s", len(rows), game_id)
            logger.info("STAGE_TIMING stage=TOTAL seconds=%.2f", _timing.perf_counter() - _run_t0)

    except LockLost as lost:
        # We no longer own this game, so we are no longer the authority on its
        # row — the worker holding the lock is, and it may be part-way through
        # its own run right now. The generic handler below would stamp `failed`
        # over that live `processing`, and the retry it schedules no-ops on the
        # held lock, so nothing would put the row right until the other worker
        # finished. Step aside instead: log, drop the lock, and let the retry
        # re-acquire it if it is genuinely free.
        logger.warning("Aborting game %s: %s", game_id, lost)
        lock.release()          # before the probe below, so we don't see ourselves
        try:
            raise self.retry(exc=lost)
        except Retry:
            raise
        except Exception:
            # Retries are spent. (Celery re-raises the `exc` we handed it rather
            # than MaxRetriesExceededError, so "not a Retry" is the reliable
            # reading of exhaustion, not the exception type.)
            #
            # The row must not be left in `processing` forever — that stranding
            # is exactly what CF-184 removed. But only write it if nobody else
            # holds the lock, and only while that proof still holds — see
            # _mark_failed_if_lock_free.
            _mark_failed_if_lock_free(str(lost))
            raise
    except Exception as exc:
        # Reporting must never break processing (CF-277). Every call that talks
        # to the database on the failure path is guarded — in two guards, not
        # one; see below — because they run from inside an `except`, so anything
        # they raise costs the `failed` write AND the retry decision, while the
        # `finally` still releases the lock. The row then sits in `processing` with nothing
        # coming back for it: CF-184's stranded game, reached by the code whose
        # job is to explain a failure.
        #
        # The rule is that every database call on this path is guarded — not
        # that one `try` covers them all. The hole here arrived by a second
        # database call being added beside a bare one, so what must survive is
        # that a new call cannot be added unguarded.
        #
        # Nothing enforces that structurally. The tests in test_worker_safety.py
        # pin the *behaviour* of the calls that exist — an outage on either one
        # still reaches the retry decision — so adding a third unguarded call
        # would break them only if it happened to fail in a covered scenario.
        # Stated because the previous version of this comment claimed the tests
        # held the rule, and they do not.
        #
        # The guards are grouped by what a failure means rather than one per
        # call: a probe that cannot answer and a report that cannot be written
        # need different responses, and a single `try` around both cannot tell
        # which raised. What that cost concretely is described just below the
        # `Retry` note.
        #
        # `raise self.retry(...)` stays OUTSIDE the guard, and that is
        # load-bearing. Celery's `Retry` inherits from `Exception` (verified:
        # Retry -> TaskPredicate -> CeleryError -> Exception), so a guard drawn
        # around it would swallow the retry signal and cause the exact stranding
        # this exists to prevent. Nothing inside the guard can raise `Retry` —
        # the `_sync_db` helpers never touch `self` — but the LockLost handler
        # above carries an `except Retry: raise` for what this file already does
        # where the two do overlap.
        # Concretely, what a shared `try` cost: a
        # blip on the probe alone skipped the message-carrying write, the
        # message-less fallback succeeded, and the row settled terminally as
        # `failed` with a NULL error_message that nothing ever repopulates. The
        # diagnostic is lost to a failure that had nothing to do with writing it.
        exists = True
        try:
            # A game deleted mid-flight is the cause of the failure (missing video
            # on retry, FK violation on save), not a transient error — abandon,
            # don't retry. This is the safety net for a deletion at any un-checked
            # point.
            exists = sync_game_exists(gid)
        except Exception:
            # Assume it exists: reporting a failure against a game that turns out
            # to be gone is harmless, while abandoning one that is still there
            # drops it silently. Same direction as the probe above the `try`.
            logger.warning(
                "Could not check whether game %s still exists — assuming it does",
                game_id, exc_info=True,
            )
        if not exists:
            # Keep the traceback: the deletion is the *likely* cause, but a
            # genuine unrelated bug that happens to coincide with one would
            # otherwise vanish without a trace. Info, not error — an abandoned
            # job isn't a failure.
            logger.info(
                "Game %s no longer exists — abandoning (no retry)", game_id,
                exc_info=True,
            )
            return

        logger.exception("Processing failed for game %s", game_id)
        reported = False
        # Reporting must never break processing. This is the only status write
        # whose payload is unbounded — `str(exc)` of an arbitrary failure — and
        # it runs from inside this `except`, so anything it raises costs the
        # `failed` write AND the retry decision below, leaving the row in
        # `processing`: CF-184's stranded-game symptom, reached by the code that
        # exists to explain a failure.
        #
        # CF-226 widened the column to Text and removes the cause. This guard is
        # not that fix repeated — it is here because the model and the database
        # can disagree. `_ERROR_MESSAGE_MAX` is read off the model at import,
        # so a worker image carrying `Text` that starts before the api has run
        # the migration turns the CF-225 clamp off while the column is still
        # varchar(1024). `clipfarm-worker` is a separate Render service with its
        # own `autoDeploy: false` and no migration in its start path — only the
        # api has a `preDeployCommand` — so worker-before-api is a deploy order
        # someone can pick, not a hypothetical.
        try:
            sync_set_game_status(gid, "failed", error_message=str(exc))
            reported = True
        except Exception:
            logger.exception(
                "Could not record the failure message for game %s — retrying the "
                "status write without it", game_id,
            )
            # A fresh Session per call, so the first one's failed transaction
            # does not poison this. Status without the message still gets the
            # row out of `processing`; the traceback is in the log above and in
            # Sentry either way.
            try:
                sync_set_game_status(gid, "failed")
                reported = True
            except Exception:
                # Say what an operator should do, and say it truthfully. Both
                # writes failed to reach this line, so `reported` is False here
                # by construction and the gate below cannot return: control
                # always reaches `self.retry`, whatever the exception type.
                #
                # That is not the same as "a retry always follows". With
                # `max_retries=2`, the third execution's `self.retry(exc=exc)`
                # re-raises `exc` and schedules nothing — the row stays in
                # `processing` and nobody comes back for it. An earlier version
                # of this comment said a retry always follows; asserting the
                # unconditional version of a conditional claim is the same
                # mistake this block exists to undo, one line up from where it
                # was made. The operator-facing string below already hedges it
                # ("if the attempts run out"); the comment now does too.
                #
                # This message used to branch to "nothing below will retry it"
                # for a permanent failure. That branch was correct when the gate
                # read the exception type alone; adding `reported` to the gate
                # made it unreachable and left it asserting the opposite of what
                # happens. An operator greps this line to decide whether to wait
                # or intervene, so it saying "nothing will retry" while a retry
                # runs is worse than saying nothing.
                #
                # Two things change what to say. Whether any attempt remains,
                # because on the last one no retry follows and "if the attempts
                # run out" is a hedge the operator has to evaluate themselves
                # while looking at the one line that already knows the answer.
                # And the exception type, which changes what a retry can
                # *achieve* rather than whether one happens.
                #
                # `self.retry` re-raises rather than scheduling once
                # `request.retries` reaches `max_retries`, so that comparison is
                # the same condition Celery is about to apply — its own test is
                # `retries > max_retries` on `request.retries + 1`, which is the
                # same boundary.
                #
                # `max_retries` may be None, which Celery reads as retry forever.
                # Nothing in this repo sets it — both tasks declare 2 and the
                # only override is a test forcing 0 — but the comparison would
                # raise TypeError if it ever were, inside this except and before
                # `self.retry`, so the original failure would be masked and the
                # row stranded: this handler doing the exact harm CF-277 exists
                # to prevent. The guard is cheaper than the reasoning about why
                # it cannot happen, and the same is true of the `+ 1` below,
                # which has the same hole.
                forever = self.max_retries is None
                last_attempt = not forever and self.request.retries >= self.max_retries
                if last_attempt:
                    next_step = (
                        "no retry follows — this was the last attempt, and the "
                        "row is still in `processing` with nothing coming back "
                        "for it; it needs a human now"
                    )
                elif isinstance(exc, PermanentPipelineError):
                    next_step = (
                        "a retry follows, but the failure itself is permanent — "
                        "the retry can only get the status written, not make the "
                        "job succeed"
                    )
                else:
                    next_step = "a retry follows and may still settle it"
                # Attempt numbers because the line's whole job is to let an
                # operator decide whether to wait or intervene.
                # `self.request.retries` is the count already made, so this is
                # 1-based on the attempt being reported, and total executions are
                # `max_retries + 1`.
                logger.exception(
                    "Could not mark game %s failed at all — it is still in "
                    "`processing` (attempt %s of %s). %s",
                    game_id,
                    self.request.retries + 1,
                    "unlimited" if forever else self.max_retries + 1,
                    next_step,
                )
        # `reported` gates this, not just the exception type. A permanent failure
        # we could not write down is still worth another attempt at writing down:
        # returning here on an unreported error is a silent strand with nothing
        # scheduled to come back for it, which is the whole bug.
        if reported and isinstance(exc, PermanentPipelineError):
            # Identical on every attempt — retrying re-runs the pipeline's most
            # expensive path to reach the same failure. Settle on `failed` now.
            logger.info("Not retrying game %s — permanent condition", game_id)
            return
        raise self.retry(exc=exc)
    finally:
        lock.release()

    # CF-194: enforce raw_upload_retention_days. Deliberately out here — past
    # the `finally`, so the advisory lock is already released, and past the
    # TemporaryDirectory, so the game's working files are gone before a sweep
    # of unrelated backlog holds this task open. Reached on the success path
    # only: every abandon path above returns, and a failure re-raises.
    try:
        _sweep_expired_raw_uploads()
    except Exception as sweep_err:
        # Belt-and-braces — the sweep swallows its own failures, and the game
        # is already `ready`, so nothing here may turn a finished run into a
        # retried one.
        logger.warning("Raw-upload retention sweep failed (%s)", sweep_err)
