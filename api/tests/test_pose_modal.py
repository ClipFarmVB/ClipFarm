"""CF-164: pose runs on Modal GPU, and degrades without it.

The worker image no longer ships torch, so the Modal path is the real one in
production and the local branch is a development convenience. These tests pin
the *routing* — which branch runs when — not the pose maths, which lives in
ml/tests and the eval harness.

Run from the api/ dir: `cd api && pytest tests/test_pose_modal.py`.
"""
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("celery")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_detect(monkeypatch):
    """Stand in for ml.pipeline.detect.

    `ml` is not importable from the api test run (working-directory is api/, and
    the package needs cv2 + numpy besides), which is precisely the situation the
    deployed worker is in for pose. Recording stubs let the fallback branches be
    exercised without either.
    """
    detect = types.ModuleType("ml.pipeline.detect")
    detect.calls = []

    def classify_within_windows(video_path, windows, **kwargs):
        detect.calls.append(("classify", video_path, windows, kwargs))
        return [dict(w, action="local") for w in windows]

    def run_detection(video_path):
        detect.calls.append(("run_detection", video_path))
        return [{"start": 0.0, "end": 5.0, "action": "local", "confidence": 0.5}]

    detect.classify_within_windows = classify_within_windows
    detect.run_detection = run_detection

    pipeline = types.ModuleType("ml.pipeline")
    pipeline.detect = detect
    ml = types.ModuleType("ml")
    ml.pipeline = pipeline

    for name, mod in (("ml", ml), ("ml.pipeline", pipeline), ("ml.pipeline.detect", detect)):
        monkeypatch.setitem(sys.modules, name, mod)
    return detect


WINDOWS = [{"start": 1.0, "end": 6.0, "action": "spike", "confidence": 0.6}]


# ── Stage 3 pose refinement ──────────────────────────────────────────────────

def test_refine_uses_modal_when_configured(monkeypatch, fake_detect):
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)
    monkeypatch.setattr(
        tasks, "_classify_windows_modal",
        lambda r2_key, windows: [dict(w, action="gpu") for w in windows],
    )

    out = tasks._refine_with_pose(Path("game.mp4"), "raw/x.mp4", WINDOWS)

    assert [w["action"] for w in out] == ["gpu"]
    assert fake_detect.calls == [], "local pose must not run when Modal succeeded"


def test_refine_falls_back_to_local_when_modal_fails(monkeypatch, fake_detect):
    """A Modal outage costs label quality, never the run — same contract as
    ball tracking (`_track_ball_cached`)."""
    from app.workers import tasks

    def boom(r2_key, windows):
        raise RuntimeError("modal down")

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)
    monkeypatch.setattr(tasks, "_classify_windows_modal", boom)

    out = tasks._refine_with_pose(Path("game.mp4"), "raw/x.mp4", WINDOWS)

    assert [w["action"] for w in out] == ["local"]
    assert fake_detect.calls[0][0] == "classify"


def test_refine_passes_configured_pose_settings_to_local(monkeypatch, fake_detect):
    from app.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)

    tasks._refine_with_pose(Path("game.mp4"), "", WINDOWS)

    _, _, _, kwargs = fake_detect.calls[0]
    assert kwargs == {
        "model_name": settings.pose_model,
        "imgsz": settings.pose_imgsz,
        "skip_frames": settings.pose_skip_frames,
    }


# ── Pose-first fallback scan ─────────────────────────────────────────────────

def test_full_scan_prefers_modal(monkeypatch, fake_detect):
    """The whole-video scan is the most expensive CPU path in the pipeline, so
    it must reach for the GPU too — not just the windowed refinement."""
    from app.workers import tasks

    remote = types.SimpleNamespace(
        remote=lambda url: [{"start": 0.0, "end": 5.0, "action": "gpu", "confidence": 0.5}]
    )
    modal_sdk = types.ModuleType("modal")
    modal_sdk.Function = types.SimpleNamespace(from_name=lambda app, fn: remote)
    storage = types.SimpleNamespace(presign_url=lambda key, expires_in: "https://signed")

    monkeypatch.setitem(sys.modules, "modal", modal_sdk)
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)
    monkeypatch.setattr("app.services.storage.presign_url", storage.presign_url)

    out = tasks._run_detection("game.mp4", "raw/x.mp4")

    assert [d["action"] for d in out] == ["gpu"]
    assert fake_detect.calls == []


def test_full_scan_falls_back_to_local(monkeypatch, fake_detect):
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)

    out = tasks._run_detection("game.mp4", "")

    assert [d["action"] for d in out] == ["local"]
    assert fake_detect.calls[0] == ("run_detection", "game.mp4")


# ── Deploy invariant ─────────────────────────────────────────────────────────

def test_worker_plan_matches_a_torchless_image():
    """`starter` is 512 MB. It is affordable only because no model ships in the
    image; a torch import there OOMs the box. The two are one decision, so pin
    them together rather than discovering the mismatch on a production deploy.
    """
    yaml = pytest.importorskip("yaml")

    dockerfile = (REPO_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    directives = [
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    ]
    installs_ml = any(
        pkg in line
        for line in directives
        for pkg in ("torch", "ultralytics", "inference==")
    )

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    if worker["plan"] == "starter":
        assert not installs_ml, (
            "Dockerfile.api installs an ML runtime again — put the worker back "
            "on `standard` in the same change (render.yaml)"
        )


def test_worker_has_no_model_cache_disk():
    """No weights are downloaded in the worker any more, so the disk that
    pinned them is gone — and with it the single-instance constraint a Render
    disk imposes (CF-65)."""
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    assert "disk" not in worker
