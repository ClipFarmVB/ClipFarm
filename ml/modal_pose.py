"""
Modal GPU deployment for pose classification (CF-164).

Second half of the GPU offload that started with ball tracking (CF-11). Pose
was the last model still running in the worker image, and it is the reason
that image carries torch + ultralytics — which at the time was why the Render
worker sat on `standard` (2 GB). Moving pose here lets the worker image drop
to ffmpeg + opencv + numpy + boto3, and lets the *full-quality* pose config
(yolov8s-pose @ 1280) come back: it was downgraded to yolov8n @ 640 in
`render.yaml` only because a 2 GB CPU box could not carry the real one.

(The worker is on `standard` again as of CF-240, but for libx264's ~400 MB
during cutting, not for an import — nothing here ships a model any more.)

All three functions call the same `ml.pipeline.detect` code the worker calls
locally, so this is an execution-location change, not a second implementation.
(The old `ml/modal_detect.py` was a second implementation — a drifted copy of
`classify_action` wired to nothing — and is deleted by this change.)

Video arrives by presigned R2 URL, same as `modal_app.py`: R2 credentials stay
in the worker and never need to exist as a Modal Secret. Refinement reads that
URL in place rather than downloading it first — see `classify_windows_remote`.

Deploy:
    modal deploy ml/modal_pose.py

Requires a Modal account + CLI auth (`modal setup`) and MODAL_TOKEN_ID /
MODAL_TOKEN_SECRET wherever this is invoked from.
"""
import logging

import modal

logger = logging.getLogger(__name__)

APP_NAME = "clipfarm-pose"

app = modal.App(APP_NAME)

# Weights are baked into the image rather than cached on a Volume (the CF-40
# pattern used by ball tracking). Pose weights are small — ~23 MB for
# yolov8s-pose, ~7 MB for yolov8n — so paying for them once at build time is
# cheaper than a Volume mount and removes cold-start download latency entirely.
# MODELS_DIR is what `_model_path()` in ml.pipeline.detect resolves bare weight
# names against, so both names below are already local at call time.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    # Everything that can change the labels is pinned, per the CF-33 lesson
    # ARCHITECTURE.md records: an unpinned resolver can "succeed" into a subtly
    # different model and the only symptom is worse output. There is no failed
    # deploy to point at, so pin rather than discover it in the eval numbers.
    # (`requests` is deliberately loose — it moves bytes, not labels.)
    .pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",
        # Same pin as ml/requirements.txt — the skeleton heuristics in
        # detect.py assume the COCO 17-keypoint layout this emits.
        "ultralytics==8.3.55",
        "opencv-python-headless==4.10.0.84",
        "numpy==1.26.4",
        "requests",
    )
    .env({"MODELS_DIR": "/models", "YOLO_CONFIG_DIR": "/tmp/ultralytics"})
    .run_commands(
        "mkdir -p /models",
        # Bare `YOLO(name)` downloads into the working directory, so cd first.
        "cd /models && python -c \""
        "from ultralytics import YOLO; YOLO('yolov8s-pose.pt'); YOLO('yolov8n-pose.pt')\"",
    )
    .add_local_python_source("ml")
)


def _download(video_url: str, dest: str) -> None:
    """Stream a presigned URL to a local path."""
    import requests

    with requests.get(video_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def _streamable(video_url: str) -> bool:
    """Can the decoder read this URL in place, seeking it with range requests?

    Probed, not assumed: it needs an https-capable FFmpeg behind OpenCV in this
    image and a store that honours `Range` (R2 does). `isOpened()` on its own is
    not the answer — a capture can open against a URL it then cannot read — so
    force one real read before believing it.

    Cheap either way: opening an mp4 fetches the header and the moov atom, not
    the file.
    """
    import cv2

    cap = cv2.VideoCapture(video_url)
    try:
        return bool(cap.isOpened() and cap.grab())
    except Exception:
        return False
    finally:
        cap.release()


@app.function(image=image, gpu="T4", timeout=1800)
def classify_windows_remote(
    video_url: str,
    windows: list[dict],
    model_name: str,
    imgsz: int,
    skip_frames: int,
) -> list[dict]:
    """
    Pose-refine rally windows on GPU. Drop-in for `classify_within_windows()`.

    `windows` and the return value are plain dicts, which is what the pipeline
    passes around anyway — nothing to serialize by hand at the RPC boundary.

    The video is read *in place* off the presigned URL when the decoder can seek
    it. Refinement visits only the rallies that survived the highlight gate — a
    fraction of a long match — so pulling the whole object down first moved
    gigabytes onto a billed T4 to be skipped. Seeking transfers roughly the
    footage actually decoded instead, and `classify_within_windows` already
    seeks per window, so nothing above this call changes.

    Expiry is not a new exposure: the presigned URL is good for an hour and this
    function's timeout is half that, so a stream outliving its signature cannot
    happen while those two numbers hold.
    """
    return _refine_from_url(video_url, windows, model_name, imgsz, skip_frames)


def _refine_from_url(
    video_url: str,
    windows: list[dict],
    model_name: str,
    imgsz: int,
    skip_frames: int,
    on_progress=None,
) -> list[dict]:
    """The body shared by `classify_windows_remote` and `classify_windows_stream`:
    pick how the source is read, then run `classify_within_windows` on it.

    One place rather than two so the seek-or-download choice cannot drift
    between the blocking and streaming entry points.
    """
    import os
    import tempfile

    from ml.pipeline.detect import classify_within_windows

    if _streamable(video_url):
        logger.info(
            "Pose refinement reading %d windows from the source URL", len(windows)
        )
        return classify_within_windows(
            video_url, windows,
            model_name=model_name, imgsz=imgsz, skip_frames=skip_frames,
            on_progress=on_progress,
        )

    # No seekable read — an image whose FFmpeg lacks https, or a store that
    # ignores Range. Fall back to the whole object rather than failing the
    # stage: it is what this did before, and refinement that runs beats
    # refinement that doesn't.
    logger.warning("Source URL is not seekable — falling back to a full download")
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, "video.mp4")
        _download(video_url, local_path)
        return classify_within_windows(
            local_path, windows,
            model_name=model_name, imgsz=imgsz, skip_frames=skip_frames,
            on_progress=on_progress,
        )


@app.function(image=image, gpu="T4", timeout=1800)
def classify_windows_stream(
    video_url: str,
    windows: list[dict],
    model_name: str,
    imgsz: int,
    skip_frames: int,
):
    """
    Same refinement as `classify_windows_remote`, but a generator: yields
    {"progress": 0-1} after each window while the GPU run is in flight (Modal
    streams them to the caller via `remote_gen`) and finally {"windows": [...]}.

    Gives the worker live progress for the bar/ETA (CF-255) — a blocking
    `.remote()` call has no channel for progress until it returns, and
    refinement is 12-16% of the bar on a Modal run, frozen for its whole
    duration. Same shape as `track_ball_stream` in `modal_app.py`: the
    callback fires from the refinement thread, a Queue bridges it to this
    generator (yield can't cross threads), and one event per rally is at most
    a few hundred events per game.

    The blocking function stays deployed beside this one: a worker running
    ahead of this deploy falls back to it when this name is not found, and a
    worker behind the worker-side change never asks for this one.
    """
    import queue
    import threading

    events: queue.Queue = queue.Queue()
    result: dict = {}

    def run() -> None:
        try:
            result["windows"] = _refine_from_url(
                video_url, windows, model_name, imgsz, skip_frames,
                on_progress=events.put,
            )
        except BaseException as exc:  # re-raised in the generator below
            result["error"] = exc
        finally:
            events.put(None)  # sentinel: refinement finished either way

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    while True:
        item = events.get()
        if item is None:
            break
        yield {"progress": float(item)}
    worker.join()

    if "error" in result:
        raise result["error"]
    yield {"windows": result["windows"]}


@app.function(image=image, gpu="T4", timeout=3600)
def detect_actions_remote(
    video_url: str,
    model_name: str,
    imgsz: int,
    skip_frames: int,
) -> list[dict]:
    """
    Full-video pose-first scan on GPU. Drop-in for `run_detection()`.

    This is the rare path — it only runs when the ball pipeline is unavailable
    or failed. It scans every skip_frames-th frame of the whole video rather
    than only the surviving rallies, hence the longer timeout.

    Takes the same POSE_* knobs as `classify_windows_remote`: the two entry
    points run on the same hardware for the same deployment, so a fallback that
    ignored them would quietly run a different config than the refinement path.

    Downloads rather than reading in place, unlike the refinement path above:
    this scan visits every skip_frames-th frame of the *whole* video, so there
    is no unread footage to save and seeking would only add round trips to a
    transfer that has to happen anyway.
    """
    import os
    import tempfile

    from ml.pipeline.detect import run_detection

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, "video.mp4")
        _download(video_url, local_path)
        # allow_stub defaults to False: if this image ever loses its pose runtime,
        # the call fails loudly rather than returning invented clips for the worker
        # to persist.
        return run_detection(
            local_path, model_name=model_name, imgsz=imgsz, skip_frames=skip_frames,
        )
