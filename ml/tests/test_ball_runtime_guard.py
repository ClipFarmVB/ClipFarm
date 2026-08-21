"""CF-225: the local ball path must say what is actually missing.

Ball tracking runs on Modal (CF-11) and the deployed image has carried no
Roboflow `inference` runtime since CF-164 — so the local-CPU fallback in
`_track_ball_cached` cannot run there at all. Left as a bare `from inference
import get_model`, a Modal outage surfaced as `ModuleNotFoundError: inference`,
which reads as a packaging bug and buries the outage that caused it.

Stdlib-only, like the rest of ml/tests: cv2 and numpy are stubbed so the module
imports without them. The guard fires on the `inference` import, before any
frame work touches either.
"""
import sys
import types

import pytest


@pytest.fixture
def ball(monkeypatch):
    """Import ml.pipeline.ball with its heavy module-level imports stubbed."""
    for name in ("cv2", "numpy"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    # Ensure the real inference package is absent — the deployed image's state.
    monkeypatch.setitem(sys.modules, "inference", None)

    sys.modules.pop("ml.pipeline.ball", None)
    import ml.pipeline.ball as module

    try:
        yield module
    finally:
        # Drop it rather than restoring it. This module object closed over the
        # fake cv2/numpy above, so leaving it in sys.modules hands the stubs to
        # whoever imports ml.pipeline.ball next. (A trailing `monkeypatch.setitem`
        # cannot do this job: it snapshots the value that is *already* there,
        # i.e. this same module, and restores it.)
        sys.modules.pop("ml.pipeline.ball", None)


def test_missing_inference_runtime_raises_a_named_error(ball):
    with pytest.raises(ball.BallRuntimeUnavailable, match="not importable in this process"):
        ball._load_model("key")


def test_the_message_does_not_assume_which_process_it_is(ball):
    """`_load_model` is the fallback path in the Celery worker and the PRIMARY
    path inside the Modal GPU image (ml/modal_app.py), where `inference` IS
    installed — so this firing there means a broken image, not a missing
    fallback. A message phrased from the worker's vantage misdirects in exactly
    the case that is a genuine bug."""
    with pytest.raises(ball.BallRuntimeUnavailable) as exc:
        ball._load_model("key")

    assert "fall back" not in str(exc.value)
    assert "clipfarm-ball-tracking" in str(exc.value), (
        "point at where the runtime does live instead"
    )


def test_the_error_is_still_caught_by_existing_broad_handling(ball):
    """A RuntimeError subclass, like detect.PoseRuntimeUnavailable. The distinct
    type separates "no runtime here" from "tracking ran and failed"; unlike the
    pose one it is NOT translated to PermanentPipelineError, because ball
    tracking has the pose-first scan left to try."""
    assert issubclass(ball.BallRuntimeUnavailable, RuntimeError)


def test_the_guard_keys_on_the_import_failing_not_on_the_package_being_installed(
    ball, monkeypatch
):
    """A half-installed `inference` imports and then fails on the attribute —
    a presence check would be satisfied, catching the import cannot be."""
    broken = types.ModuleType("inference")

    def __getattr__(name):
        raise ImportError("cannot import name 'get_model'")

    broken.__getattr__ = __getattr__
    monkeypatch.setitem(sys.modules, "inference", broken)

    assert sys.modules["inference"] is broken
    with pytest.raises(ball.BallRuntimeUnavailable):
        ball._load_model("key")
