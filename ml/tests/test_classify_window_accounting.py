"""CF-164: refinement must report what it actually did, and notice a dead stream.

`classify_within_windows` logged `len(refined)` — the input length — so a run
that decoded zero frames still read as a full success. Since refinement reads
the presigned URL in place (review 6) rather than a fully-downloaded file, a
mid-run network fault is indistinguishable from end-of-video to `cap.read()`,
which made that misreporting reachable rather than theoretical.

Stdlib-only, like the rest of ml/tests: cv2, numpy and ultralytics are stubbed,
so this exercises the loop's bookkeeping without a decoder or a GPU.
"""
import sys
import types

import pytest


class FakeCapture:
    """Minimal cv2.VideoCapture: yields `frames` reads, then fails forever."""

    def __init__(self, frames, fps=30.0):
        self._left = frames
        self._fps = fps
        self._pos_ms = 0.0
        self.released = False

    def isOpened(self):
        return True

    def set(self, prop, value):
        self._pos_ms = value  # seek: POS_MSEC

    def get(self, prop):
        return {"FPS": self._fps, "W": 1920.0, "H": 1080.0, "POS_MSEC": self._pos_ms}[prop]

    def read(self):
        if self._left <= 0:
            return False, None
        self._left -= 1
        self._pos_ms += 1000.0 / self._fps
        return True, object()

    def release(self):
        self.released = True


@pytest.fixture
def detect(monkeypatch):
    cv2 = types.ModuleType("cv2")
    cv2.CAP_PROP_FPS, cv2.CAP_PROP_FRAME_WIDTH = "FPS", "W"
    cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_POS_MSEC = "H", "POS_MSEC"
    cv2.VideoCapture = None  # each test substitutes its own
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    if "numpy" not in sys.modules:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))

    ultra = types.ModuleType("ultralytics")
    ultra.YOLO = lambda name: (lambda *a, **k: [])   # model(frame) -> no results
    monkeypatch.setitem(sys.modules, "ultralytics", ultra)

    sys.modules.pop("ml.pipeline.detect", None)
    import ml.pipeline.detect as module
    monkeypatch.setitem(sys.modules, "ml.pipeline.detect", module)
    module._cv2 = cv2
    return module


WINDOWS = [
    {"start": 0.0, "end": 2.0, "action": "spike", "confidence": 0.7},
    {"start": 10.0, "end": 12.0, "action": "dig", "confidence": 0.7},
    {"start": 20.0, "end": 22.0, "action": "set", "confidence": 0.7},
]


def test_reports_windows_scored_not_windows_visited(detect, monkeypatch, caplog):
    """The whole point: a run that decodes frames but scores nothing must not
    report the input length as if it were work done."""
    import logging

    monkeypatch.setattr(detect._cv2, "VideoCapture", lambda p: FakeCapture(frames=10**6))

    with caplog.at_level(logging.INFO):
        out = detect.classify_within_windows("http://signed/v.mp4", list(WINDOWS))

    assert len(out) == len(WINDOWS), "shape of the return value is unchanged"
    msgs = " | ".join(r.getMessage() for r in caplog.records)
    assert "pose signal on 0/3 windows" in msgs, msgs
    assert "no window produced a pose signal" in msgs, "a total no-op must say so"


def test_dead_stream_raises_instead_of_reporting_success(detect, monkeypatch):
    """Frames, then nothing. Previously every remaining window silently kept its
    trajectory label and the run still reported the full count."""
    monkeypatch.setattr(detect._cv2, "VideoCapture", lambda p: FakeCapture(frames=5))

    with pytest.raises(detect.PoseStreamError, match="cannot continue"):
        detect.classify_within_windows("http://signed/v.mp4", list(WINDOWS))


def test_dead_stream_releases_the_capture(detect, monkeypatch):
    """The raise happens mid-loop, past the normal cap.release()."""
    cap = FakeCapture(frames=5)
    monkeypatch.setattr(detect._cv2, "VideoCapture", lambda p: cap)

    with pytest.raises(detect.PoseStreamError):
        detect.classify_within_windows("http://signed/v.mp4", list(WINDOWS))
    assert cap.released, "a leaked capture holds the connection open"


def test_a_video_that_never_decodes_is_not_a_stream_error(detect, monkeypatch):
    """Zero frames from the start is a different condition — an empty or
    unreadable source, not a stream that died mid-run. It must not raise."""
    monkeypatch.setattr(detect._cv2, "VideoCapture", lambda p: FakeCapture(frames=0))

    out = detect.classify_within_windows("http://signed/v.mp4", list(WINDOWS))
    assert [w["action"] for w in out] == ["spike", "dig", "set"]
