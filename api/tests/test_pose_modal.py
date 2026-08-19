"""CF-164: pose runs on Modal GPU, and degrades without it.

The worker image no longer ships torch, so the Modal path is the real one in
production and the local branch is a development convenience. These tests pin
the *routing* — which branch runs when — not the pose maths, which lives in
ml/tests and the eval harness.

Run from the api/ dir: `cd api && pytest tests/test_pose_modal.py`.
"""
import re
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

    def run_detection(video_path, **kwargs):
        detect.calls.append(("run_detection", video_path, kwargs))
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

def _fake_modal(monkeypatch, remote_fn):
    modal_sdk = types.ModuleType("modal")
    modal_sdk.Function = types.SimpleNamespace(
        from_name=lambda app, fn: types.SimpleNamespace(remote=remote_fn)
    )
    monkeypatch.setitem(sys.modules, "modal", modal_sdk)
    monkeypatch.setattr(
        "app.services.storage.presign_url", lambda key, expires_in: "https://signed"
    )


def test_full_scan_prefers_modal(monkeypatch, fake_detect):
    """The whole-video scan is the most expensive CPU path in the pipeline, so
    it must reach for the GPU too — not just the windowed refinement."""
    from app.workers import tasks

    _fake_modal(
        monkeypatch,
        lambda url, model, imgsz, skip: [
            {"start": 0.0, "end": 5.0, "action": "gpu", "confidence": 0.5}
        ],
    )
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)

    out = tasks._run_detection("game.mp4", "raw/x.mp4")

    assert [d["action"] for d in out] == ["gpu"]
    assert fake_detect.calls == []


def test_full_scan_sends_configured_pose_settings_to_modal(monkeypatch, fake_detect):
    """Both entry points run on the same GPU for the same deployment, so the
    fallback scan must honour POSE_* rather than silently running its own
    hardcoded config."""
    from app.config import settings
    from app.workers import tasks

    seen = {}

    def remote(url, model, imgsz, skip):
        seen.update(url=url, model=model, imgsz=imgsz, skip=skip)
        return []

    _fake_modal(monkeypatch, remote)
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)

    tasks._run_detection("game.mp4", "raw/x.mp4")

    assert seen == {
        "url": "https://signed",
        "model": settings.pose_model,
        "imgsz": settings.pose_imgsz,
        "skip": settings.pose_skip_frames,
    }


def test_full_scan_refuses_to_fabricate_clips_without_a_pose_runtime(
    monkeypatch, fake_detect
):
    """The critical one.

    `run_detection` degrades to `_stub_detections` when ultralytics is missing:
    up to 15 invented clips at invented timestamps, flat 0.75 confidence — which
    this pipeline would cut, upload and persist as genuine highlights. That was
    harmless while the image always carried ultralytics; since CF-164 it does
    not, so the stub sat one Modal failure away from production data.

    Failing the game is the correct outcome here, and the loud one: no ball
    pipeline, no GPU and no pose runtime means there is genuinely nothing to
    detect. This test asserts the refusal, not the stub.
    """
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)
    monkeypatch.setattr("app.config.settings.debug", False)

    with pytest.raises(RuntimeError, match="fabricated stub detections"):
        tasks._run_detection("game.mp4", "")

    assert fake_detect.calls == [], "run_detection must not be reached at all"


def test_full_scan_still_runs_locally_in_debug(monkeypatch, fake_detect):
    """The stub is a development affordance and stays reachable under DEBUG,
    where fake clips are the point."""
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)
    monkeypatch.setattr("app.config.settings.debug", True)

    out = tasks._run_detection("game.mp4", "")

    from app.config import settings

    assert [d["action"] for d in out] == ["local"]
    kind, path, kwargs = fake_detect.calls[0]
    assert (kind, path) == ("run_detection", "game.mp4")
    assert kwargs == {
        "model_name": settings.pose_model,
        "imgsz": settings.pose_imgsz,
        "skip_frames": settings.pose_skip_frames,
    }


def test_refine_skips_modal_entirely_when_no_rallies_survived(monkeypatch, fake_detect):
    """An empty window list must not cold-start a T4 and pull the whole video
    (up to 2 GB) only to hand back []."""
    from app.workers import tasks

    def unexpected(r2_key, windows):
        raise AssertionError("Modal must not be called for zero windows")

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)
    monkeypatch.setattr(tasks, "_classify_windows_modal", unexpected)

    assert tasks._refine_with_pose(Path("game.mp4"), "raw/x.mp4", []) == []
    assert fake_detect.calls == []


# ── Deploy invariant ─────────────────────────────────────────────────────────

ML_RUNTIME_PACKAGES = ("torch", "torchvision", "ultralytics", "transformers", "inference")
ML_RUNTIME_PACKAGES_SET = set(ML_RUNTIME_PACKAGES)


def _requirements_packages(path: Path) -> set[str]:
    """Package names declared by a requirements file, following nested `-r`."""
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r"):
            nested = line[2:].strip()
            names |= _requirements_packages((path.parent / nested).resolve())
            continue
        names.add(re.split(r"[\[<>=!;\s]", line, 1)[0].lower())
    return names


def _image_installs_ml_runtime() -> bool:
    """Whether Dockerfile.api pulls an ML runtime, by literal name OR via `-r`.

    Grepping the Dockerfile's own lines is not enough: `pip install -r <file>`
    hides the package list in another file, and ml/requirements.txt already
    declares torch that way. Resolving the referenced files is what makes this
    check bind on the regression it exists to catch.
    """
    dockerfile = (REPO_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    directives = [
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    ]

    # COPY <src> <dst> — the dst paths are what `pip install -r` later names.
    copied: dict[str, Path] = {}
    for line in directives:
        parts = line.split()
        if parts[:1] == ["COPY"] and len(parts) >= 3 and parts[-2].endswith(".txt"):
            copied[parts[-1]] = REPO_ROOT / parts[-2]

    installed: set[str] = set()
    for line in directives:
        for token in re.findall(r"-r\s+(\S+)", line):
            source = copied.get(token)
            assert source is not None, (
                f"Dockerfile.api installs `-r {token}`, which this test cannot trace "
                "back to a repo file — the image's dependency surface is unknown, so "
                "the worker's plan cannot be justified. Add the mapping here."
            )
            installed |= _requirements_packages(source)
        # Literal `pip install foo==1.2` on the Dockerfile's own line.
        if "pip install" in line:
            for token in re.findall(r"[\"']?([A-Za-z0-9_.-]+)[\"']?(?:[=<>~]|\s|$)", line):
                installed.add(token.lower())

    return any(pkg in installed for pkg in ML_RUNTIME_PACKAGES)


def test_worker_plan_matches_a_torchless_image():
    """`starter` is 512 MB. It is affordable only because no model ships in the
    image; a torch import there OOMs the box. The two are one decision, so pin
    them together rather than discovering the mismatch on a production deploy.
    """
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    if worker["plan"] == "starter":
        assert not _image_installs_ml_runtime(), (
            "Dockerfile.api installs an ML runtime again — put the worker back "
            "on `standard` in the same change (render.yaml)"
        )


def test_invariant_detects_an_ml_runtime_hidden_behind_a_requirements_file():
    """The guard above is only worth having if it survives the indirect form.

    ml/requirements.txt declares torch, so `pip install -r` of it must trip the
    check exactly as a literal `pip install torch` would.
    """
    assert "torch" in _requirements_packages(REPO_ROOT / "ml" / "requirements.txt")
    assert "ultralytics" in _requirements_packages(REPO_ROOT / "ml" / "requirements.txt")
    # api/requirements.txt is what the image actually installs — and must stay clean.
    in_api = ML_RUNTIME_PACKAGES_SET & _requirements_packages(
        REPO_ROOT / "api" / "requirements.txt"
    )
    assert not in_api, f"api/requirements.txt now pulls an ML runtime: {in_api}"


def test_dev_compose_declares_every_named_volume_it_mounts():
    """Removing the model_cache volume broke `docker compose config` in review:
    the top-level declaration went, the worker's mount stayed, and the documented
    dev path wouldn't start at all. A dangling mount is invisible in a diff and
    obvious here."""
    yaml = pytest.importorskip("yaml")

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    declared = set(compose.get("volumes") or {})
    mounted = {
        mount.split(":")[0]
        for service in compose["services"].values()
        for mount in (service.get("volumes") or [])
        if isinstance(mount, str) and not mount.startswith((".", "/", "~"))
    }

    assert not (mounted - declared), (
        f"docker-compose.yml mounts undeclared volume(s): {mounted - declared}"
    )


def test_worker_has_no_model_cache_disk():
    """No weights are downloaded in the worker any more, so the disk that
    pinned them is gone — and with it the single-instance constraint a Render
    disk imposes (CF-65)."""
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    assert "disk" not in worker
