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
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# One definition of how an `-r` include is spelled, shared with the pre-commit
# gate rather than copied. Duplicated, drifting requirements parsing is what
# this branch spent a round removing; re-adding a second copy here would be the
# same mistake in a new place.
from scripts.check_dev_set import INCLUDE

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

    class PoseRuntimeUnavailable(RuntimeError):
        pass

    detect.classify_within_windows = classify_within_windows
    detect.run_detection = run_detection
    detect.PoseRuntimeUnavailable = PoseRuntimeUnavailable
    # Default: a pose runtime IS present, so the local fallback is real and the
    # degradation marker stays quiet. Tests that care override this.
    detect.pose_available = lambda: True

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


def test_degraded_pose_is_logged_findably_with_the_game_id(monkeypatch, fake_detect, caplog):
    """Modal down + no local pose runtime = trajectory-only labels, which is
    below the pre-CF-164 baseline (local pose always ran). That must not pass as
    one more warning line: ERROR is what the Sentry logging integration promotes
    to an event, and the game id is what makes degraded games re-runnable.
    """
    import logging

    from app.workers import tasks

    fake_detect.pose_available = lambda: False
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)
    monkeypatch.setattr(
        tasks, "_classify_windows_modal",
        lambda r2_key, windows: (_ for _ in ()).throw(RuntimeError("modal down")),
    )

    with caplog.at_level(logging.ERROR):
        out = tasks._refine_with_pose(Path("g.mp4"), "raw/x.mp4", WINDOWS, "game-abc")

    assert out == WINDOWS, "windows pass through untouched — labels are not invented"
    marker = [r for r in caplog.records if "POSE_DEGRADED" in r.getMessage()]
    assert len(marker) == 1, "expected exactly one findable marker"
    assert marker[0].levelno == logging.ERROR, "WARNING would not reach Sentry"
    assert "game-abc" in marker[0].getMessage()
    assert fake_detect.calls == [], "no point calling local pose with no runtime"


def test_no_marker_when_a_pose_runtime_is_actually_present(monkeypatch, fake_detect, caplog):
    """The marker means degraded, not merely 'Modal was skipped'. A dev box with
    ultralytics installed falls back locally and is not degraded."""
    import logging

    from app.workers import tasks

    fake_detect.pose_available = lambda: True
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)

    with caplog.at_level(logging.ERROR):
        tasks._refine_with_pose(Path("g.mp4"), "raw/x.mp4", WINDOWS, "game-abc")

    assert not [r for r in caplog.records if "POSE_DEGRADED" in r.getMessage()]
    assert fake_detect.calls[0][0] == "classify"


def test_missing_pose_runtime_is_a_permanent_error(monkeypatch, fake_detect):
    """`run_detection`'s refusal is identical on every attempt, so it is
    translated at the boundary into the type the task handler declines to retry
    — rather than re-running the pipeline's priciest path twice more."""
    from app.workers import tasks

    def boom(video_path, **kwargs):
        from ml.pipeline.detect import PoseRuntimeUnavailable
        raise PoseRuntimeUnavailable("no pose runtime")

    fake_detect.run_detection = boom
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: False)

    with pytest.raises(tasks.PermanentPipelineError):
        tasks._run_detection("game.mp4", "")


# ── The cross-process boundary ───────────────────────────────────────────────

def test_numpy_scalars_never_reach_the_modal_boundary(monkeypatch):
    """A numpy-2 pickle names `numpy._core`, which a numpy-1.x image cannot
    import, so a version split fails at *deserialization* on the far side — and
    the caller sees only "Modal failed", then silently skips pose because there
    is no local runtime. It is also data-dependent: `classify_contact_action`
    computes `min(0.88, 0.65 + sp_after / 1500.0)` inside a branch guarded by
    `sp_after > 180.0` (`ball.py:394`), so the numpy operand wins the `min()`
    only between ~180 and ~345 px/s. Slower contacts never reach the expression
    and faster ones take the plain 0.88.

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
        # Exactly the expression ball.py evaluates, at a velocity that actually
        # reaches it: the branch needs `vy_after > 60.0` (strict) as well as
        # `sp_after > 180.0`, so hypot(200, 70) ≈ 212 clears both, and sits
        # below the ~345 where the constant 0.88 wins the min() instead.
        "confidence": round(min(0.88, 0.65 + np.hypot(np.float64(200.0), 70.0) / 1500.0), 2),
        "labels": ["spike"],
        # The np.int64 *key* matters as much as the values: keys ride the same
        # pickle, and a copy that unwraps values only would ship it green.
        "features": {"contact_count": np.int64(3), "speeds": np.array([1.5, 2.5]),
                     "contacts_by_player": {np.int64(0): 2}},
    }]
    assert isinstance(windows[0]["confidence"], np.floating), "fixture must reproduce the leak"

    tasks._classify_windows_modal("raw/x.mp4", windows)

    def assert_plain(value, path="windows"):
        assert not hasattr(value, "dtype"), f"numpy object survived to the wire at {path}"
        if isinstance(value, dict):
            for k, v in value.items():
                assert_plain(k, f"{path}.<key {k!r}>")
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


# ── How the GPU reads the video ──────────────────────────────────────────────

@pytest.fixture
def modal_pose(monkeypatch):
    """`ml/modal_pose.py` with the Modal SDK faked out.

    The module builds its App and Image at import time, so it cannot be imported
    without the SDK installed. Behind a pass-through decorator the two entry
    points are ordinary functions, which is the only part these tests need — the
    same trick `fake_detect` plays for `ml`.
    """
    import importlib.util

    class _Chain:
        """Every Image builder call returns the Image, so one object covers all."""
        def __getattr__(self, _name):
            return lambda *a, **kw: self

    class _App:
        def function(self, *a, **kw):
            return lambda fn: fn

    sdk = types.ModuleType("modal")
    sdk.App = lambda *a, **kw: _App()
    sdk.Image = _Chain()
    monkeypatch.setitem(sys.modules, "modal", sdk)

    spec = importlib.util.spec_from_file_location(
        "modal_pose_under_test", REPO_ROOT / "ml" / "modal_pose.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refinement_reads_the_source_in_place_rather_than_downloading_it(
    monkeypatch, modal_pose, fake_detect
):
    """Refinement visits only the rallies that survived the highlight gate.

    Downloading the whole object first moved gigabytes onto a billed T4 to be
    skipped; seeking the presigned URL transfers roughly the footage actually
    decoded.
    """
    monkeypatch.setattr(modal_pose, "_streamable", lambda url: True)

    def never(*args, **kwargs):
        raise AssertionError("pulled the whole object down instead of seeking it")

    monkeypatch.setattr(modal_pose, "_download", never)

    url = "https://r2.example/raw/x.mp4?sig=abc"
    out = modal_pose.classify_windows_remote(url, WINDOWS, "yolov8s-pose.pt", 1280, 4)

    kind, video_path, windows, kwargs = fake_detect.calls[0]
    assert kind == "classify"
    assert video_path == url, "the decoder must be pointed at the source, not a copy"
    assert windows == WINDOWS, "window times are absolute — nothing is remapped"
    assert [w["action"] for w in out] == ["local"]


def test_refinement_falls_back_to_a_download_when_the_url_cannot_be_seeked(
    monkeypatch, modal_pose, fake_detect
):
    """An image whose FFmpeg lacks https, or a store that ignores Range.

    Refinement that runs beats refinement that doesn't, so this degrades to the
    old behaviour instead of failing the stage.
    """
    monkeypatch.setattr(modal_pose, "_streamable", lambda url: False)
    pulled = {}
    monkeypatch.setattr(
        modal_pose, "_download", lambda url, dest: pulled.update(url=url, dest=dest)
    )

    url = "https://r2.example/raw/x.mp4?sig=abc"
    modal_pose.classify_windows_remote(url, WINDOWS, "yolov8s-pose.pt", 1280, 4)

    assert pulled["url"] == url
    _, video_path, _, _ = fake_detect.calls[0]
    assert video_path == pulled["dest"], "must decode the copy it just pulled down"


def test_the_full_scan_still_downloads(monkeypatch, modal_pose, fake_detect):
    """No unread footage to save here, so seeking would only add round trips.

    Pinned because the refinement path above now looks like the thing to copy.
    """
    monkeypatch.setattr(modal_pose, "_streamable", lambda url: True)
    pulled = {}
    monkeypatch.setattr(
        modal_pose, "_download", lambda url, dest: pulled.update(url=url, dest=dest)
    )

    url = "https://r2.example/raw/x.mp4?sig=abc"
    modal_pose.detect_actions_remote(url, "yolov8s-pose.pt", 1280, 4)

    kind, video_path, _ = fake_detect.calls[0]
    assert kind == "run_detection"
    assert video_path == pulled["dest"]


@pytest.mark.parametrize(
    "opened, grabbed, expected",
    [(True, True, True), (True, False, False), (False, False, False)],
)
def test_streamable_demands_a_real_read_not_just_an_open(
    monkeypatch, modal_pose, opened, grabbed, expected
):
    """A capture can open against a URL it then cannot read a frame from.

    Believing `isOpened()` alone would route the stage down a path that yields
    nothing and silently returns the windows unrefined.
    """
    released = {"n": 0}

    class _Cap:
        def __init__(self, _url): pass
        def isOpened(self): return opened
        def grab(self): return grabbed
        def release(self): released["n"] += 1

    cv2 = types.ModuleType("cv2")
    cv2.VideoCapture = _Cap
    monkeypatch.setitem(sys.modules, "cv2", cv2)

    assert modal_pose._streamable("https://r2.example/x.mp4") is expected
    assert released["n"] == 1, "the probe must not leak a capture"


# ── Deploy invariant ─────────────────────────────────────────────────────────

def _code_lines(path: Path) -> str:
    """A file's non-comment lines, rejoined — shared by both pin checks.

    Full-line comments only, deliberately: an inline trailing comment naming a
    second version (`"numpy==2.3.5",  # not 1.26.4`) must still be seen, or a
    pin could be hidden from these checks by writing one. Rejoined with a
    newline so the horizontal-whitespace class the patterns below use cannot
    pair a package name on one line with a version on the next.

    One definition rather than two: the version guard below and the consistency
    guard read the same files, and a review round pointed out that the drift
    between their preprocessing is exactly what the version guard exists to
    catch (CF-278).

    Adjacent string literals are spliced, because Python splices them: pip sees
    one requirement from `"numpy" "<2"`, and a reader of the file sees two
    tokens. Without this, every pattern below stops at the closing quote and
    reads that as an unconstrained `numpy` — a *false allow*, which is the one
    class of error these guards cannot afford. The seam is only removed
    between two quotes of the same kind with nothing but horizontal whitespace
    between them, so `"numpy", "requests"` (a comma intervenes) still reads as
    two requirements.
    """
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    return re.sub(r"([\"'])[^\S\n]*\1", "", text)


# What `inference==1.3.3` will accept. The ball image's numpy has to land in
# here or the image does not build (CF-359).
INFERENCE_NUMPY_WINDOW = SpecifierSet(">=2.0.0,<2.4.0")


def _admits_a_numpy_2(constraint: str) -> bool:
    """Whether a numpy requirement admits any version inference would accept.

    Real PEP 440 semantics via `packaging` rather than arithmetic over version
    tuples. The hand-rolled comparator this replaces was sound for the two
    versions it sampled and wrong for every other point in the window — it
    rejected `numpy==2.1.0` while asserting it "admits no numpy 2" — and the
    dependency is free: `packaging` is one of pytest's own requirements, so it
    is present anywhere this suite runs.

    Candidates are the window's endpoints plus every version literal the
    constraint itself names. That is what makes an exact pin work: the only
    version `numpy==2.1.0` admits is one the constraint told us about.
    """
    try:
        specifier = SpecifierSet(constraint)
    except InvalidSpecifier as exc:  # a typo should be loud, not permissive
        raise AssertionError(
            f"ml/modal_app.py has an unparseable numpy constraint {constraint!r}: {exc}"
        ) from exc

    candidates = {Version("2.0.0"), Version("2.3.5")}
    for literal in re.findall(r"[0-9][0-9A-Za-z.*+!]*", constraint):
        try:
            candidates.add(Version(literal.rstrip(".*")))
        except InvalidVersion:
            continue
    return any(
        v in INFERENCE_NUMPY_WINDOW and specifier.contains(v, prereleases=True)
        for v in candidates
    )


ML_RUNTIME_PACKAGES = ("torch", "torchvision", "ultralytics", "transformers", "inference")
ML_RUNTIME_PACKAGES_SET = set(ML_RUNTIME_PACKAGES)


def _requirements_packages(path: Path) -> set[str]:
    """Package names declared by a requirements file, following nested `-r`."""
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # All three include spellings pip accepts, via the gate's own pattern:
        # `-r <file>` alone would silently treat `--requirement <file>` as a
        # package named `--requirement` and drop everything behind the include.
        inc = INCLUDE.fullmatch(line)
        if inc:
            names |= _requirements_packages((path.parent / inc.group(1)).resolve())
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
    # requirements-dev.txt must pin numpy — the dev pin IS the card (CF-276),
    # and requiring it here is what makes deleting it, loosening it to a range
    # or hiding it behind an environment marker a failure rather than a skip.
    # It has no business pinning opencv, so for every other package it is
    # checked only if it names the package at all.
    sources = [
        ("Dockerfile.api", REPO_ROOT / "Dockerfile.api", True),
        ("ml/requirements.txt", REPO_ROOT / "ml" / "requirements.txt", True),
        ("ml/modal_pose.py", REPO_ROOT / "ml" / "modal_pose.py", True),
        ("api/requirements-dev.txt", REPO_ROOT / "api" / "requirements-dev.txt",
         package == "numpy"),
    ]
    if package != "numpy":
        # ml/modal_app.py joins for opencv and is deliberately absent for
        # numpy. Not an oversight, and not laziness: `inference==1.3.3`
        # requires numpy>=2.0.0,<2.4.0, so that image *cannot* carry the
        # 1.26.4 the others do — `pip install inference==1.3.3 numpy==1.26.4`
        # is ResolutionImpossible. Listing it here for numpy would assert
        # something the resolver forbids, so the split is recorded as a
        # deliberate island rather than hidden as a passing test. CF-278 (#323)
        # posed the choice; it was decided in favour of migrating the repo to
        # numpy 2, which is CF-363 (#443). Until that lands this stays an
        # island, and CF-364 (#447) pins it to a numpy inference accepts.
        # opencv has no such constraint on the headless distribution, so the
        # pin there is both possible and load-bearing — see its comment.
        sources.append(
            ("ml/modal_app.py", REPO_ROOT / "ml" / "modal_app.py", True)
        )
    # Whitespace around the operator because pip accepts it, and a reformatted
    # pin must fail as a split or a removal rather than vanish. `[^\S\n]` and
    # not `\s`: the text below is comment-stripped lines rejoined with "\n", so
    # `\s` would let a match run across two lines and pair a package name with
    # the next line's version.
    pattern = re.compile(
        re.escape(package) + r"[^\S\n]*==[^\S\n]*([0-9][0-9A-Za-z.\-]*)"
    )

    found = {}
    for label, path, required in sources:
        matches = set(pattern.findall(_code_lines(path)))
        if not matches:
            assert not required, (
                f"{label} does not pin {package} — it must, see this test's docstring"
            )
            continue
        assert len(matches) == 1, f"{label} pins {package} to several versions: {matches}"
        found[label] = matches.pop()

    assert len(set(found.values())) == 1, f"{package} version split across runtimes: {found}"


def test_the_ball_image_may_not_pin_numpy_below_2():
    """The other half of the CF-278 island, asserted rather than commented.

    `ml/modal_app.py` is excluded from the numpy leg of the pin-consistency
    test above because `inference==1.3.3` requires `numpy>=2.0.0,<2.4.0`, so it
    cannot carry the 1.26.4 the other runtimes pin. That exclusion means the
    test above would happily accept `numpy==1.26.4` there — the natural edit for
    anyone reading CF-278's title, and one that makes `modal deploy` fail at
    image build with a ResolutionImpossible, which is a slow and confusing way
    to learn it.

    So this asserts the constraint directly: constrain numpy there or do not,
    but any constraint has to admit a version `inference==1.3.3` accepts. Three
    versions of this guard were each defeated by one spelling further out —
    `numpy<2` past an `==`-only check, `numpy>=1.26,<2` hiding its cap behind a
    floor, `'numpy<2'` in single quotes past a double-quote anchor — so it
    anchors on the package name alone and evaluates the whole specifier set
    with `packaging`, rather than pattern-matching operators. A fourth defeat
    came from the other side: `numpy[cffi]==1.26.4`, `numpy @ <a 1.26.4 wheel
    url>` and `"numpy" "<2"` each ended the match at an empty capture, which is
    `SpecifierSet("")` and admits everything, so all three passed. Extras and
    direct references are now matched, and `_code_lines` splices adjacent
    string literals.

    The repo-wide numpy-2 migration — decided on CF-278 (#323) and carried by
    CF-363 (#443) — makes this test agree with the one above rather than
    needing to be deleted.
    """
    # Anchored on the package name, and on nothing else. Anchoring on a double
    # quote — which an earlier version of this did — made `'numpy<2'` in single
    # quotes invisible, and nothing in this repo enforces double quotes: neither
    # ruff config selects the `Q` rules and no formatter runs. The name anchor
    # also keeps a pin inside a `run_commands("pip install ...")` in scope,
    # which the quote anchor had silently dropped.
    #
    # The whole comma-separated specifier set is captured, not the first
    # operator: `numpy>=1.26,<2` is the ecosystem-standard spelling of "numpy 1
    # only", and a pattern that stops at the floor never sees the cap behind it.
    #
    # Extras and direct references are matched rather than ignored, and for the
    # same reason the seam-splicing in `_code_lines` exists: anything the
    # pattern does not consume between the name and the version ends the match
    # at an *empty* capture, and an empty capture is `SpecifierSet("")`, which
    # admits everything. So `numpy[cffi]==1.26.4` and
    # `numpy @ https://.../numpy-1.26.4-...whl` both read as an unconstrained
    # numpy and pass — the failure mode this guard is least allowed to have.
    for extras, reference, constraint in re.findall(
        r"(?<![A-Za-z0-9_.\-])numpy(?![A-Za-z0-9_.\-])"
        r"(\[[^\]\n]*\])?"
        r"([^\S\n]*@[^\s,'\"]*)?"
        r"((?:[^\S\n]*(?:===|==|~=|!=|<=|>=|<|>)[^\S\n]*[^\s,'\"]+[^\S\n]*,?)*)",
        _code_lines(REPO_ROOT / "ml" / "modal_app.py"),
        re.IGNORECASE,
    ):
        # A direct reference names one artefact, so `packaging` has no
        # specifier to evaluate and the URL's version is a filename
        # convention rather than metadata. Fail loudly instead of guessing:
        # a false rejection blocks a legal spelling nobody uses here, where a
        # false allow ships an image that cannot build.
        assert not reference.strip(), (
            f'ml/modal_app.py installs numpy by direct reference ("numpy'
            f'{extras}{reference}"), whose version this guard cannot check. '
            "Use a version specifier inference==1.3.3 accepts "
            "(>=2.0.0,<2.4.0)."
        )
        assert _admits_a_numpy_2(constraint), (
            f'ml/modal_app.py constrains numpy as "numpy{extras}{constraint}", '
            "which admits no version inference==1.3.3 accepts "
            "(>=2.0.0,<2.4.0), so this cannot resolve and the image build "
            "fails with ResolutionImpossible. See CF-359 (#440), and CF-363 "
            "(#443) for the repo-wide numpy-2 migration."
        )


def test_the_ball_image_pins_opencv_where_inference_can_follow():
    """The ball image's opencv pin has a ceiling the consistency test can't see.

    `test_pipeline_deps_are_pinned_to_one_version_everywhere` asserts the four
    opencv sources agree with each other. Agreement is necessary and not
    sufficient here: `inference==1.3.3` requires
    `opencv-python<=4.10.0.84,>=4.8.1.78` and the same for
    `opencv-contrib-python`, and both land in this image alongside the headless
    distribution — three distributions writing one `cv2`, last install wins.
    Bumping all four sources to a newer headless in one edit keeps them
    agreeing and quietly restores the major-version split the pin removed,
    because the two `inference` brings are pinned by `inference`, not by us.

    So this asserts the ball image's headless pin lands inside the window
    `inference` forces its siblings into. Raising it means moving off
    `inference==1.3.3` (CF-33 pins that deliberately), which is a decision, not
    a bump.
    """
    window = SpecifierSet(">=4.8.1.78,<=4.10.0.84")
    pins = re.findall(
        r"(?<![A-Za-z0-9_.\-])opencv-python-headless(?![A-Za-z0-9_.\-])"
        r"[^\S\n]*==[^\S\n]*([0-9][0-9A-Za-z.\-]*)",
        _code_lines(REPO_ROOT / "ml" / "modal_app.py"),
        re.IGNORECASE,
    )
    assert pins, (
        "ml/modal_app.py no longer pins opencv-python-headless with a plain "
        "`==`. Unpinned, pip resolves it to a 5.x while inference==1.3.3 holds "
        "opencv-python and opencv-contrib-python at 4.10.0.84 in the same "
        "image — see CF-359 (#440)."
    )
    for pin in pins:
        assert Version(pin) in window, (
            f"ml/modal_app.py pins opencv-python-headless=={pin}, which is "
            "outside the >=4.8.1.78,<=4.10.0.84 that inference==1.3.3 forces "
            "on opencv-python and opencv-contrib-python in the same image. "
            "All three write `cv2` and the last install wins, so a headless "
            "outside that window is the major-version split again, however "
            "well the other pin sites agree with it (CF-359, #440)."
        )


def _load_compose(path: Path):
    """Parse a Compose file, tolerating its `!override` / `!reset` merge tags.

    `yaml.safe_load` refuses unknown tags outright, which would make this test
    skip the one file it most needs to read.
    """
    yaml = pytest.importorskip("yaml")

    class ComposeLoader(yaml.SafeLoader):
        pass

    def passthrough(loader, tag_suffix, node):
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node, deep=True)
        return loader.construct_scalar(node)

    ComposeLoader.add_multi_constructor("!", passthrough)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)


def test_worker_never_ships_permission_to_fabricate_clips_on_render():
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


def test_worker_never_ships_permission_to_fabricate_clips_on_the_vps():
    """Same invariant, the other deploy path — and a sharper version of it.

    On that box api and worker share a single `.env.docker`, so a flag set to
    troubleshoot the api reaches the worker directly, with no env group in
    between. `environment:` wins over `env_file:`, so pinning here is what makes
    the override hold. ALLOW_STUB_DETECTIONS is the load-bearing one: DEBUG was
    pinned first and, on its own, pins the wrong flag.
    """
    compose = _load_compose(REPO_ROOT / "docker-compose.prod.yml")
    env = compose["services"]["worker"].get("environment") or {}

    assert str(env.get("ALLOW_STUB_DETECTIONS", "")).lower() == "false", (
        "docker-compose.prod.yml must pin ALLOW_STUB_DETECTIONS — .env.docker is "
        "shared with the api, so leaving it unset lets a debugging session grant "
        "the worker permission to persist fabricated clips"
    )
    assert str(env.get("DEBUG", "")).lower() == "false"


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


# ── Ball tracking fallback (CF-225) ──────────────────────────────────

@pytest.fixture
def fake_ball(monkeypatch, tmp_path):
    """Stand in for ml.pipeline.ball, plus a storage layer that always misses.

    Same reason as `fake_detect`: `ml` is not importable from the api test run,
    and neither is a Roboflow runtime — which is the deployed worker's state for
    ball tracking too, and the whole subject of these tests.
    """
    ball = types.ModuleType("ml.pipeline.ball")

    class BallRuntimeUnavailable(RuntimeError):
        pass

    class BallPosition:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class TrackedBall:
        def __init__(self, positions=None):
            self.positions = positions or []

    def track_ball(video_path, api_key, **kwargs):
        raise BallRuntimeUnavailable("no local ball runtime")

    ball.MODEL_ID = "volleyball/3"
    ball.BallRuntimeUnavailable = BallRuntimeUnavailable
    ball.BallPosition = BallPosition
    ball.TrackedBall = TrackedBall
    ball.track_ball = track_ball

    pipeline = types.ModuleType("ml.pipeline")
    pipeline.ball = ball
    ml = types.ModuleType("ml")
    ml.pipeline = pipeline

    for name, mod in (("ml", ml), ("ml.pipeline", pipeline), ("ml.pipeline.ball", ball)):
        monkeypatch.setitem(sys.modules, name, mod)

    # The real module, patched function by function. A sys.modules entry does
    # NOT work here: `from app.services import storage` resolves the attribute
    # the parent package gained when the submodule was first imported, so once
    # anything in the run has imported it the stub is never consulted. These
    # tests were passing on the real `download_file` failing for want of R2
    # credentials — green for a reason unrelated to what they assert.
    from app.services import storage

    def download_file(key, dest):
        raise RuntimeError("ball cache miss")

    monkeypatch.setattr(storage, "download_file", download_file)
    monkeypatch.setattr(storage, "upload_file", lambda *a, **kw: None)
    monkeypatch.setenv("ROBOFLOW_API_KEY", "rf-key")
    return ball


def _modal_down(monkeypatch, tasks):
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda key: True)

    def boom(*args, **kwargs):
        raise RuntimeError("modal 503")

    monkeypatch.setattr(tasks, "_track_ball_modal", boom)


def test_modal_failure_survives_the_missing_local_ball_runtime(monkeypatch, tmp_path, fake_ball):
    """The Modal error is the half an operator can act on, so it has to survive
    the fallback attempt. Before CF-225 it was swallowed and the game failed with
    `ModuleNotFoundError: inference` — which reads as a broken image, not as an
    outage."""
    from app.workers import tasks

    _modal_down(monkeypatch, tasks)

    with pytest.raises(RuntimeError) as exc:
        tasks._track_ball_cached(
            tmp_path / "game.mp4", tmp_path, sample_every=3,
            r2_key="raw/x.mp4", video_md5="abc",
        )

    assert "modal 503" in str(exc.value), "the Modal failure must survive"
    assert "no local ball runtime" in str(exc.value), "so must the local one"
    assert "modal 503" in str(exc.value.__cause__), (
        "chain from the Modal error, not from the missing-runtime one"
    )


def test_the_local_failures_type_survives_the_wrapping(monkeypatch, tmp_path, fake_ball):
    """"No runtime here" is true whether or not Modal was tried first, and a
    deployed run ALWAYS tries Modal — so flattening the type to RuntimeError
    erased the distinction in the only environment where the condition is
    expected. It cannot suppress a retry: nothing translates it to
    PermanentPipelineError, unlike the pose equivalent."""
    from app.workers import tasks

    _modal_down(monkeypatch, tasks)

    with pytest.raises(fake_ball.BallRuntimeUnavailable) as exc:
        tasks._track_ball_cached(
            tmp_path / "game.mp4", tmp_path, sample_every=3,
            r2_key="raw/x.mp4", video_md5="abc",
        )

    assert "modal 503" in str(exc.value)


def test_any_local_failure_carries_the_modal_cause(monkeypatch, tmp_path, fake_ball):
    """Not just the missing runtime. Locally — dev, the eval harness — the local
    attempt really runs, so it can fail on a corrupt file or a rejected Roboflow
    key, and the Modal failure is no less relevant for that. A plain failure
    stays a plain failure, though: only the runtime case keeps the named type."""
    from app.workers import tasks

    _modal_down(monkeypatch, tasks)

    def cannot_open(*args, **kwargs):
        raise RuntimeError("Cannot open video: game.mp4")

    monkeypatch.setattr(fake_ball, "track_ball", cannot_open)

    with pytest.raises(RuntimeError) as exc:
        tasks._track_ball_cached(
            tmp_path / "game.mp4", tmp_path, sample_every=3,
            r2_key="raw/x.mp4", video_md5="abc",
        )

    assert "modal 503" in str(exc.value)
    assert "Cannot open video" in str(exc.value)
    assert not isinstance(exc.value, fake_ball.BallRuntimeUnavailable)


def test_a_missing_roboflow_key_is_not_dressed_up_as_a_tracking_failure(
    monkeypatch, tmp_path, fake_ball
):
    """`process_game` guards on the key, but the eval and diagnose paths call
    straight in here. Read inside the try, a missing key came back as "…and
    locally ('ROBOFLOW_API_KEY')" — a config error wearing an outage's clothes."""
    from app.workers import tasks

    _modal_down(monkeypatch, tasks)
    monkeypatch.delenv("ROBOFLOW_API_KEY")

    with pytest.raises(KeyError):
        tasks._track_ball_cached(
            tmp_path / "game.mp4", tmp_path, sample_every=3,
            r2_key="raw/x.mp4", video_md5="abc",
        )


# ── The pose-first hand-off (CF-225) ───────────────────────────────────

BALL_ERR = "BallRuntimeUnavailable: failed on Modal (modal 503) and locally (no runtime)"


def test_a_permanent_pose_failure_reports_the_ball_failure_before_it(monkeypatch):
    """The end of the chain, and the only place both halves are in scope.

    Everything upstream is invisible without this: the caller catches the ball
    failure and falls through, and the pose scan's PermanentPipelineError is
    what reaches `error_message` and Sentry. Its message is about pose."""
    from app.workers import tasks

    def unavailable(video_path, r2_key):
        raise tasks.PermanentPipelineError("Pose detection is unavailable (no ultralytics)")

    monkeypatch.setattr(tasks, "_run_detection", unavailable)

    with pytest.raises(tasks.PermanentPipelineError) as exc:
        tasks._pose_first_fallback("game.mp4", "raw/x.mp4", BALL_ERR)

    assert "Pose detection is unavailable" in str(exc.value)
    assert "ball tracking failed first" in str(exc.value)
    assert "modal 503" in str(exc.value), "the whole chain, not just the last link"


def test_a_transient_pose_failure_reports_it_too_without_becoming_permanent(monkeypatch):
    """`_run_detection` only translates PoseRuntimeUnavailable, so catching just
    PermanentPipelineError missed every other way the scan dies — a corrupt
    file, a decode error mid-run, an OOM — and dropped the ball failure in
    exactly the case this exists to prevent. Permanence still has to follow the
    original: a transient failure that came back permanent would lose its
    retry."""
    from app.workers import tasks

    def cannot_open(video_path, r2_key):
        raise RuntimeError("Cannot open video: game.mp4")

    monkeypatch.setattr(tasks, "_run_detection", cannot_open)

    with pytest.raises(RuntimeError) as exc:
        tasks._pose_first_fallback("game.mp4", "raw/x.mp4", BALL_ERR)

    assert "Cannot open video" in str(exc.value)
    assert "ball tracking failed first" in str(exc.value)
    assert not isinstance(exc.value, tasks.PermanentPipelineError), (
        "wrapping must not turn a retryable failure into a permanent one"
    )


def test_the_pose_failure_is_untouched_when_ball_tracking_never_ran(monkeypatch):
    """No ROBOFLOW_API_KEY: pose-first is the plan, not a fallback, so there is
    no earlier failure to report and nothing to add."""
    from app.workers import tasks

    original = tasks.PermanentPipelineError("Pose detection is unavailable")

    def unavailable(video_path, r2_key):
        raise original

    monkeypatch.setattr(tasks, "_run_detection", unavailable)

    with pytest.raises(tasks.PermanentPipelineError) as exc:
        tasks._pose_first_fallback("game.mp4", "", None)

    assert exc.value is original


def test_the_scan_result_passes_straight_through(monkeypatch):
    from app.workers import tasks

    monkeypatch.setattr(tasks, "_run_detection", lambda v, k: [{"start": 0.0}])

    assert tasks._pose_first_fallback("game.mp4", "raw/x.mp4", BALL_ERR) == [{"start": 0.0}]


def test_worker_has_no_model_cache_disk():
    """No weights are downloaded in the worker any more, so the disk that
    pinned them is gone — and with it the single-instance constraint a Render
    disk imposes (CF-65)."""
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    assert "disk" not in worker
