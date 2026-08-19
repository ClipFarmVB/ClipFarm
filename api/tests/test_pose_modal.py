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


@pytest.mark.parametrize("allowed", [False, True])
def test_full_scan_forwards_the_stub_permission(monkeypatch, fake_detect, allowed):
    """The stub-refusal contract, from the caller's side.

    The refusal itself lives in `run_detection` (see
    ml/tests/test_detect_stub_guard.py), keyed on the pose import actually
    failing rather than on ultralytics being installed — a broken torch fails
    that import with the package present, which a find_spec check would miss.
    What the worker owes is the *permission*, because this pipeline persists
    whatever comes back.
    """
    from app.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)
    monkeypatch.setattr("app.config.settings.allow_stub_detections", allowed)

    out = tasks._run_detection("game.mp4", "")

    assert [d["action"] for d in out] == ["local"]
    kind, path, kwargs = fake_detect.calls[0]
    assert (kind, path) == ("run_detection", "game.mp4")
    assert kwargs == {
        "model_name": settings.pose_model,
        "imgsz": settings.pose_imgsz,
        "skip_frames": settings.pose_skip_frames,
        "allow_stub": allowed,
    }


def test_debug_does_not_grant_permission_to_fabricate_clips(monkeypatch, fake_detect):
    """DEBUG is a general dev-fallbacks flag and it lives in the env group shared
    by the api and the worker — the group exists to set things for both. Turning
    it on to troubleshoot the api must not hand the worker permission to write
    invented highlights, so the two switches are deliberately separate.
    """
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)
    monkeypatch.setattr("app.config.settings.debug", True)
    monkeypatch.setattr("app.config.settings.allow_stub_detections", False)

    tasks._run_detection("game.mp4", "")

    assert fake_detect.calls[0][2]["allow_stub"] is False


def test_stub_detections_are_off_by_default():
    """The default is the whole protection for anyone who never sets the var."""
    from app.config import Settings

    assert Settings().allow_stub_detections is False


# ── The cross-process boundary ───────────────────────────────────────────────

def test_numpy_scalars_never_reach_the_modal_boundary(monkeypatch):
    """A numpy-2 pickle names `numpy._core`, which a numpy-1.x image cannot
    import, so a version split fails at *deserialization* on the far side — and
    the caller sees only "Modal failed", then silently skips pose because there
    is no local runtime. It is also data-dependent: `classify_contact_action`
    computes `min(0.88, 0.65 + sp_after / 1500.0)`, so the numpy operand only
    wins the `min()` below ~345 px/s and faster contacts serialize fine.

    Pins keep the two images aligned; this keeps the payload plain regardless.
    """
    np = pytest.importorskip("numpy")
    from app.workers import tasks

    sent = {}

    def remote(url, windows, model, imgsz, skip):
        sent["windows"] = windows
        return windows

    _fake_modal(monkeypatch, remote)

    windows = [{
        "start": np.float64(1.0),
        "end": 6.0,
        "action": "spike",
        # Exactly the expression ball.py evaluates for a slower contact.
        "confidence": round(min(0.88, 0.65 + np.hypot(np.float64(120.0), 60.0) / 1500.0), 2),
        "labels": ["spike"],
        "features": {"contact_count": np.int64(3), "speeds": np.array([1.5, 2.5])},
    }]
    assert isinstance(windows[0]["confidence"], np.floating), "fixture must reproduce the leak"

    tasks._classify_windows_modal("raw/x.mp4", windows)

    def assert_plain(value, path="windows"):
        assert not hasattr(value, "dtype"), f"numpy object survived to the wire at {path}"
        if isinstance(value, dict):
            for k, v in value.items():
                assert_plain(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                assert_plain(v, f"{path}[{i}]")

    assert_plain(sent["windows"])
    assert sent["windows"][0]["features"]["speeds"] == [1.5, 2.5]


def test_json_safe_leaves_ordinary_payloads_untouched():
    from app.workers.tasks import _json_safe

    payload = [{"a": 1, "b": 2.5, "c": "x", "d": None, "e": [1, {"f": True}]}]
    assert _json_safe(payload) == payload



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


@pytest.mark.parametrize("package", ["numpy", "opencv-python-headless"])
def test_pipeline_deps_are_pinned_to_one_version_everywhere(package):
    """Pipeline code runs in the worker AND in the Modal image, and rally dicts
    cross that boundary as a pickle. A numpy-2 pickle names `numpy._core`, which
    a numpy-1.x image cannot import — so a version split between these files
    fails at deserialization on the far side, which the worker can only report as
    "Modal failed". `_json_safe` keeps the payload plain so this can't bite, but
    the two runtimes disagreeing is its own bug; catch it here rather than in a
    silent label regression.
    """
    sources = {
        "Dockerfile.api": REPO_ROOT / "Dockerfile.api",
        "ml/requirements.txt": REPO_ROOT / "ml" / "requirements.txt",
        "ml/modal_pose.py": REPO_ROOT / "ml" / "modal_pose.py",
    }
    pattern = re.compile(re.escape(package) + r"==([0-9][0-9A-Za-z.\-]*)")

    found = {}
    for label, path in sources.items():
        text = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        matches = set(pattern.findall(text))
        assert matches, f"{label} does not pin {package} — it must, see this test's docstring"
        assert len(matches) == 1, f"{label} pins {package} to several versions: {matches}"
        found[label] = matches.pop()

    assert len(set(found.values())) == 1, f"{package} version split across runtimes: {found}"


def test_worker_never_ships_permission_to_fabricate_clips():
    """Neither switch may be enabled on the deployed worker, and DEBUG is pinned
    rather than merely left unset: the worker inherits `clipfarm-shared`, so a
    flag added there for the api arrives on the process that writes to the
    database."""
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")
    env = {e["key"]: e.get("value") for e in worker["envVars"] if "key" in e}

    assert env.get("DEBUG") == "false", "worker must pin DEBUG, not inherit it"
    assert str(env.get("ALLOW_STUB_DETECTIONS", "false")).lower() == "false"

    # And the shared group must not define either one, since both services read it.
    shared = next(
        g for g in render["envVarGroups"] if g["name"] == "clipfarm-shared"
    )
    shared_keys = {e["key"] for e in shared["envVars"]}
    assert not shared_keys & {"DEBUG", "ALLOW_STUB_DETECTIONS"}, (
        "these are per-service decisions — defining them in the shared group "
        "applies them to the worker too"
    )


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
