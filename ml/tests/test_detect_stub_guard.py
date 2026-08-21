"""CF-164: `run_detection` must not fabricate clips unless asked to.

`_stub_detections` invents clips at invented timestamps with a flat 0.75
confidence. That is a development affordance, but the worker persists whatever
`run_detection` returns, and since CF-164 the deployed image has no pose runtime
— so the stub sat one Modal failure away from being written to the database as
genuine highlights.

Stdlib-only, like the rest of ml/tests: cv2 and numpy are stubbed so the module
imports without them (the CI job installs neither). Nothing here touches either
stub — the guard fires on the *ultralytics* import, well before any frame work.
"""
import sys
import types

import pytest


@pytest.fixture
def detect(monkeypatch):
    """Import ml.pipeline.detect with its heavy module-level imports stubbed."""
    for name in ("cv2", "numpy"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    # Ensure the real ultralytics is absent, which is the deployed image's state.
    monkeypatch.setitem(sys.modules, "ultralytics", None)

    sys.modules.pop("ml.pipeline.detect", None)
    import ml.pipeline.detect as module

    try:
        yield module
    finally:
        # See the same teardown in test_ball_runtime_guard.py: this module holds
        # the fake cv2/numpy, and the `monkeypatch.setitem` that used to sit here
        # restored the stub-bound module rather than removing it.
        sys.modules.pop("ml.pipeline.detect", None)


def test_refuses_to_fabricate_clips_by_default(detect):
    """The default matters more than the flag: a caller that forgets to think
    about this gets the safe behaviour."""
    with pytest.raises(RuntimeError, match="fabricated clips"):
        detect.run_detection("game.mp4")


def test_allow_stub_is_off_by_default_in_the_signature(detect):
    import inspect

    param = inspect.signature(detect.run_detection).parameters["allow_stub"]
    assert param.default is False


def test_stub_is_still_reachable_when_explicitly_allowed(detect, monkeypatch):
    """Development still gets its fake clips \u2014 that is what the flag is for."""
    calls = []
    monkeypatch.setattr(
        detect, "_stub_detections", lambda path: calls.append(path) or [{"stub": True}]
    )

    assert detect.run_detection("game.mp4", allow_stub=True) == [{"stub": True}]
    assert calls == ["game.mp4"]


def test_guard_keys_on_the_import_failing_not_on_ultralytics_being_installed(
    detect, monkeypatch
):
    """A broken torch fails `from ultralytics import YOLO` with ultralytics
    present and importable. Checking for the package (find_spec) would miss that;
    catching the import cannot."""
    broken = types.ModuleType("ultralytics")

    def __getattr__(name):
        raise ImportError("libtorch_cpu.so: cannot open shared object file")

    broken.__getattr__ = __getattr__
    monkeypatch.setitem(sys.modules, "ultralytics", broken)

    # The package is present and importable — a presence check would be satisfied.
    assert sys.modules["ultralytics"] is broken
    with pytest.raises(RuntimeError, match="fabricated clips"):
        detect.run_detection("game.mp4")
