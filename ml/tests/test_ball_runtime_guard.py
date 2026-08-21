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

    monkeypatch.setitem(sys.modules, "ml.pipeline.ball", module)
    return module


def test_missing_inference_runtime_raises_a_named_error(ball):
    with pytest.raises(ball.BallRuntimeUnavailable, match="no Roboflow"):
        ball._load_model("key")


def test_the_error_is_recognisable_as_permanent(ball):
    """Same shape as detect.PoseRuntimeUnavailable: a RuntimeError subclass, so
    existing broad handling still catches it, with a distinct type for callers
    that should not retry — no retry makes `inference` appear."""
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
