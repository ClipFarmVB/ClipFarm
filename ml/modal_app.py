"""
Modal GPU deployment for ball tracking (CF-11).

Ball tracking is the pipeline's dominant cost: ~27-42 min on CPU per game
(frame-by-frame RF-DETR inference). This module deploys the exact same
`track_ball()` used locally onto a T4 GPU, where the same workload runs in
low single-digit minutes.

Deploy:
    modal deploy ml/modal_app.py

Requires a Modal account + CLI auth (`modal setup`) and MODAL_TOKEN_ID /
MODAL_TOKEN_SECRET set wherever this is deployed from or invoked. The
Roboflow API key is passed as a plain argument (not a Modal Secret) to keep
initial setup simple — same key already lives in worker env as
ROBOFLOW_API_KEY. Tightening this to a proper Modal Secret is a follow-up,
not a blocker.
"""
import modal

APP_NAME = "clipfarm-ball-tracking"
FUNCTION_NAME = "track_ball_remote"

app = modal.App(APP_NAME)

# Persistent weight cache (same lesson as CF-40): without this, every cold
# container re-downloads the RF-DETR weights (~114MB) from Roboflow's CDN.
model_cache = modal.Volume.from_name("clipfarm-model-cache", create_if_missing=True)

# CUDA devel base (not debian_slim): inference's GPU ONNX path needs pycuda,
# which compiles against CUDA headers at install time.
image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("libgl1", "libglib2.0-0", "ffmpeg")
    .pip_install(
        "torch",
        "torchvision",
        "opencv-python-headless",
        "numpy",
        "requests",
        # Pinned per CF-33: unpinned, pip resolves to a version predating
        # RF-DETR support and the ball model silently fails to load.
        "inference==1.3.3",
    )
    # Separate layer: inference==1.3.3 pins CPU-only ONNX extras; the GPU
    # execution path requires the cu12 extras (onnxruntime-gpu + pycuda).
    # pycuda compiles from source, so keep it isolated for cache-friendly
    # iteration. Same ~=0.29.7 constraint inference itself uses.
    # CC/CXX: Modal's standalone python defaults to clang, which the nvidia
    # image doesn't ship — point the pycuda build at gcc/g++ instead.
    .env({"CC": "gcc", "CXX": "g++"})
    .pip_install("inference-models[onnx-cu12]~=0.29.7")
    .add_local_python_source("ml")
)


@app.function(
    image=image,
    gpu="T4",
    volumes={"/models": model_cache},
    timeout=1800,  # 30 min ceiling — generous headroom over the expected ~5 min run
)
def track_ball_remote(video_url: str, api_key: str, sample_every: int) -> list[dict]:
    """
    Download a video from a presigned URL, run ball tracking on GPU, return
    positions as plain dicts (JSON-safe for the RPC boundary).

    Mirrors `_track_ball_cached`'s local path exactly so the caller can treat
    this as a drop-in replacement for `track_ball()`.
    """
    import os
    import tempfile
    from dataclasses import asdict

    import requests

    from ml.pipeline.ball import track_ball

    # Route both inference caches at the persistent volume (CF-40 pattern).
    os.environ.setdefault("INFERENCE_HOME", "/models/roboflow")
    os.environ.setdefault("MODEL_CACHE_DIR", "/models/roboflow")

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, "video.mp4")
        with requests.get(video_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)

        tracker = track_ball(local_path, api_key, sample_every=sample_every)
        return [asdict(p) for p in tracker.positions]
