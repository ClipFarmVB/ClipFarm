"""ffmpeg thread bounding in ml/pipeline/clip.py (CF-224).

The container's cgroup allows half a CPU while `nproc` reports the host's 16, so
an unpinned x264 spawns ~24 encoder threads and its frame buffers OOM-kill the
worker. Every encode and decode must therefore carry an explicit `-threads`.

ffmpeg-python is stubbed rather than skipped-around: the assertion is about the
arguments clip.py builds, and a real ffmpeg would only make the test slow and
dependent on a binary CI need not have.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline import clip as clip_mod  # noqa: E402


class _Stream:
    """Minimal stand-in for ffmpeg-python's fluent chain."""

    def __init__(self, calls: dict):
        self._calls = calls

    def output(self, *args, **kwargs):
        self._calls["output"].append(kwargs)
        return self

    def overwrite_output(self):
        return self

    def run(self, *args, **kwargs):
        return b"", b""


class _FakeFFmpeg:
    def __init__(self):
        self.calls = {"input": [], "output": []}

    def input(self, *args, **kwargs):
        self.calls["input"].append(kwargs)
        return _Stream(self.calls)

    def probe(self, path):
        return {"format": {"duration": "12.0"}}


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    fake = _FakeFFmpeg()
    monkeypatch.setitem(sys.modules, "ffmpeg", fake)
    return fake


def _thread_values(fake: _FakeFFmpeg) -> list[int | None]:
    return [c.get("threads") for c in fake.calls["input"] + fake.calls["output"]]


def test_generate_clips_bounds_threads(fake_ffmpeg, tmp_path):
    detections = [{"start": 10.0, "end": 32.0, "action": "spike", "confidence": 0.9}]

    clip_mod.generate_clips(str(tmp_path / "game.mp4"), detections, tmp_path, threads=2)

    # Clip cut + thumbnail, each an input and an output.
    assert _thread_values(fake_ffmpeg) == [2, 2, 2, 2]


def test_generate_condensed_video_bounds_threads(fake_ffmpeg, tmp_path):
    windows = [(0.0, 5.0), (20.0, 30.0)]

    clip_mod.generate_condensed_video(str(tmp_path / "game.mp4"), windows, tmp_path, threads=2)

    # One encode per window; the concat stitch is stream-copy, so it decodes and
    # encodes nothing and needs no bound.
    per_part = [c.get("threads") for c in fake_ffmpeg.calls["input"][:2]]
    per_part += [c.get("threads") for c in fake_ffmpeg.calls["output"][:2]]
    assert per_part == [2, 2, 2, 2]


def test_recut_single_bounds_threads(fake_ffmpeg, tmp_path):
    clip_mod.recut_single(str(tmp_path / "game.mp4"), 10.0, 32.0, tmp_path, threads=2)

    assert _thread_values(fake_ffmpeg) == [2, 2, 2, 2]


def test_default_is_bounded_when_caller_passes_nothing(fake_ffmpeg, tmp_path):
    """A bare pipeline call must not fall back to ffmpeg's host-sized default."""
    detections = [{"start": 0.0, "end": 4.0, "action": "spike", "confidence": 0.5}]

    clip_mod.generate_clips(str(tmp_path / "game.mp4"), detections, tmp_path)

    assert clip_mod.DEFAULT_THREADS > 0
    assert set(_thread_values(fake_ffmpeg)) == {clip_mod.DEFAULT_THREADS}


@pytest.mark.parametrize("bad", [0, -1])
def test_a_thread_count_that_is_not_a_bound_falls_back(fake_ffmpeg, tmp_path, bad):
    """0 is ffmpeg's *auto* sentinel — the host-sized pool this module avoids —
    and a negative fails every cut inside the swallowing except. Neither may
    reach ffmpeg."""
    detections = [{"start": 0.0, "end": 4.0, "action": "spike", "confidence": 0.5}]

    clip_mod.generate_clips(str(tmp_path / "game.mp4"), detections, tmp_path, threads=bad)

    assert set(_thread_values(fake_ffmpeg)) == {clip_mod.DEFAULT_THREADS}
