"""
Modal GPU deployment for pose classification (CF-164).

Second half of the GPU offload that started with ball tracking (CF-11). Pose
was the last model still running in the worker image, and it is the reason
that image carries torch + ultralytics — which in turn is why the Render
worker sits on `standard` (2 GB). Moving pose here lets the worker image drop
to ffmpeg + opencv + numpy + boto3, and lets the *full-quality* pose config
(yolov8s-pose @ 1280) come back: it was downgraded to yolov8n @ 640 in
`render.yaml` only because a 2 GB CPU box could not carry the real one.

Both functions call the same `ml.pipeline.detect` code the worker calls
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
        )


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


@app.function(image=image, gpu="T4", timeout=3600)
def extract_keypoints_remote(
    video_url: str,
    model_name: str,
    imgsz: int,
    sample_fps: float,
    max_seconds: float | None = None,
) -> dict:
    """
    Full-video keypoint pass on GPU, for the condense stage (CF-198).

    Drop-in for `ml.pipeline.detect.extract_keypoints`, and the only way that
    pass can run in production: the worker image dropped torch in CF-164, so
    `pose_available()` is False there and the local branch is a development
    path only.

    Downloads rather than reading in place, for the same reason
    `detect_actions_remote` does: this samples the whole video end to end, so
    there is no unread footage for range requests to save and seeking would
    only add round trips to a transfer that has to happen anyway.

    `max_seconds` exists to time this without paying for a whole game — see the
    cost table in ml/eval/README.md. It is not used by the pipeline.
    """
    import os
    import tempfile

    from ml.pipeline.detect import extract_keypoints

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, "video.mp4")
        _download(video_url, local_path)
        return extract_keypoints(
            local_path,
            model_name=model_name,
            imgsz=imgsz,
            sample_fps=sample_fps,
            max_seconds=max_seconds,
        )
