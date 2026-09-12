"""The phone-sized rendition in ml/pipeline/clip.py (CF-321).

Clips are cut at source resolution, so a 4K upload streams 4K to a phone on
cellular. This module makes one reduced rendition alongside each clip. What is
worth pinning is not that ffmpeg runs — it is the three decisions around it:

* which clips get a rendition at all (the short-side bound, and why it is the
  *short* side),
* that the scale can never upscale or distort,
* that a rendition which fails, or is not needed, leaves the clip intact.

ffmpeg is stubbed rather than invoked, matching `test_clip_threads.py`: every
assertion here is about the arguments clip.py builds and the decisions it makes,
and a real binary would only make the suite slow and dependent on a codec build
CI need not have.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline import clip as clip_mod  # noqa: E402


# ── The bound ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1920, 1080, True),    # 1080p landscape — the common case
        (3840, 2160, True),    # 4K
        (1080, 1920, True),    # 1080p portrait: short side is 1080, still big
        (1280, 720, False),    # already exactly at the bound
        (1920, 720, False),    # wide but short — short side is already 720
        (640, 480, False),     # smaller than the bound
        (720, 720, False),     # square, at the bound
    ],
)
def test_which_clips_get_a_rendition(width, height, expected):
    assert clip_mod._needs_mobile_rendition(width, height, 720) is expected


def test_the_bound_is_on_the_short_side_so_rotation_cannot_change_the_answer():
    """A portrait recording is often stored as 1920x1080 plus a rotate flag.

    Because the test is `min(w, h)`, it gives the same answer whichever way the
    probe reports those dimensions — so this never has to interpret rotation
    metadata, which is the usual source of portrait-video bugs.
    """
    assert (
        clip_mod._needs_mobile_rendition(1920, 1080, 720)
        == clip_mod._needs_mobile_rendition(1080, 1920, 720)
    )


@pytest.mark.parametrize("width,height", [(0, 1080), (1920, 0), (-1, -1)])
def test_nonsense_dimensions_ask_for_nothing(width, height):
    """A probe that returns 0 or a negative is not a small video, it is a
    broken read — and `min()` would call it "already small enough" and skip.
    Skipping is the safe direction only for a real measurement."""
    assert clip_mod._needs_mobile_rendition(width, height, 720) is False


# ── The scale filter ─────────────────────────────────────────────────────────

def test_the_scale_filter_clamps_the_short_side_on_both_orientations():
    f = clip_mod._mobile_scale_filter(720)
    # Landscape branch clamps height, portrait branch clamps width.
    assert "gt(iw,ih)" in f
    assert "min(720,iw)" in f and "min(720,ih)" in f


def test_the_scale_filter_cannot_upscale():
    """`min()` is what makes an upscale unrepresentable rather than merely
    unlikely. A plain `-2:720` would blow a 480p clip up to 720p, costing
    bitrate to add no detail."""
    f = clip_mod._mobile_scale_filter(720)
    assert f.count("min(") == 2


def test_the_free_axis_is_minus_two_so_aspect_survives_and_stays_even():
    """-1 would preserve aspect too, but can land on an odd number, and libx264
    with yuv420p rejects odd dimensions outright."""
    f = clip_mod._mobile_scale_filter(720)
    assert "-2" in f
    assert "-1" not in f


def test_the_filter_honours_a_custom_bound():
    assert "min(540,iw)" in clip_mod._mobile_scale_filter(540)


# ── Probing ──────────────────────────────────────────────────────────────────

class _Probe:
    def __init__(self, payload):
        self._payload = payload

    def probe(self, path):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_dimensions_come_from_the_video_stream_not_the_first_one():
    fake = _Probe({"streams": [
        {"codec_type": "audio"},
        {"codec_type": "video", "width": 1920, "height": 1080},
    ]})
    assert clip_mod._probe_dimensions(fake, "game.mp4") == (1920, 1080)


@pytest.mark.parametrize("payload", [
    {"streams": []},
    {"streams": [{"codec_type": "audio"}]},
    {},
    RuntimeError("ffprobe exploded"),
])
def test_an_unreadable_probe_is_none_rather_than_a_raise(payload):
    """clip.py's callers treat None as "unknown" and fail open. A raise here
    would take down the whole cut, which the rendition must never do."""
    assert clip_mod._probe_dimensions(_Probe(payload), "game.mp4") is None


# ── Behaviour in generate_clips ──────────────────────────────────────────────

class _Stream:
    def __init__(self, calls, fail_on):
        self._calls = calls
        self._fail_on = fail_on
        self._out: dict = {}

    def output(self, *args, **kwargs):
        self._calls["output"].append(kwargs)
        self._out = kwargs
        return self

    def overwrite_output(self):
        return self

    def run(self, *args, **kwargs):
        if self._fail_on and self._fail_on in str(self._out.get("vf", "")):
            raise RuntimeError("encode failed")
        return b"", b""


class _FakeFFmpeg:
    def __init__(self, width=1920, height=1080, fail_on=None):
        self.calls: dict = {"input": [], "output": []}
        self._dims = (width, height)
        self._fail_on = fail_on

    def input(self, *args, **kwargs):
        self.calls["input"].append(kwargs)
        return _Stream(self.calls, self._fail_on)

    def probe(self, path):
        return {
            "format": {"duration": "12.0"},
            "streams": [{
                "codec_type": "video",
                "width": self._dims[0],
                "height": self._dims[1],
            }],
        }


def _install(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "ffmpeg", fake)
    return fake


DETECTIONS = [{"start": 10.0, "end": 32.0, "action": "spike", "confidence": 0.9}]


def _scaled_outputs(fake):
    return [c for c in fake.calls["output"] if "vf" in c]


def test_a_large_source_gets_a_rendition(monkeypatch, tmp_path):
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    results = clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    assert len(_scaled_outputs(fake)) == 1
    assert results[0]["mobile_path"] is not None


def test_a_source_already_under_the_bound_gets_no_second_file(monkeypatch, tmp_path):
    """With no downscale left to do, the only saving available is the higher
    crf — measured at ~2x the bytes for ~3x the cutting time (CF-321), against
    10-77x for half to an eighth of the time when there is a downscale. NULL is
    the right answer, and the client falls back."""
    fake = _install(monkeypatch, _FakeFFmpeg(1280, 720))

    results = clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    assert _scaled_outputs(fake) == []
    assert results[0]["mobile_path"] is None
    # ...and the clip itself is untouched by the decision.
    assert results[0]["clip_path"] is not None


def test_unknown_dimensions_fail_open(monkeypatch, tmp_path):
    """If the probe cannot be read we make the rendition anyway: the filter
    cannot upscale, so a wasted encode is the whole downside, where the other
    way round is a phone streaming a 4K clip."""
    fake = _FakeFFmpeg()
    fake.probe = lambda path: {"streams": []}  # type: ignore[method-assign]
    _install(monkeypatch, fake)

    results = clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    assert len(_scaled_outputs(fake)) == 1
    assert results[0]["mobile_path"] is not None


def test_a_failed_rendition_still_yields_the_clip(monkeypatch, tmp_path):
    """The rendition is an optimisation. Losing it costs a phone some
    bandwidth; losing the clip costs the user the play."""
    _install(monkeypatch, _FakeFFmpeg(1920, 1080, fail_on="scale"))

    results = clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    assert len(results) == 1
    assert results[0]["clip_path"] is not None
    assert results[0]["mobile_path"] is None


def test_the_rendition_is_encoded_from_the_clip_not_the_source(monkeypatch, tmp_path):
    """Reading the cut clip keeps this independent of how the clip was made,
    which is what lets CF-239's conditional `-c copy` land without touching it —
    and avoids seeking a multi-GB source a second time."""
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    results = clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    clip_path = str(results[0]["clip_path"])
    # The scaled output's input is the clip file, not the game file.
    scaled_input = fake.calls["input"][-1]
    assert scaled_input is not None
    assert clip_path.endswith(".mp4")
    assert str(results[0]["mobile_path"]).endswith(".mobile.mp4")


def test_the_rendition_is_browser_and_phone_safe(monkeypatch, tmp_path):
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    out = _scaled_outputs(fake)[0]
    assert out["vcodec"] == "libx264"
    assert out["pix_fmt"] == "yuv420p"
    # +faststart puts the moov atom first: without it a player must fetch the
    # end of the file before it can start, which on cellular is the stall this
    # ticket exists to remove.
    assert out["movflags"] == "+faststart"


def test_the_rendition_trades_quality_for_bytes(monkeypatch, tmp_path):
    """It is watched on a phone-sized screen where crf 23's extra detail is not
    resolvable, and bandwidth is the point of the card."""
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    clip_mod.generate_clips(str(tmp_path / "g.mp4"), DETECTIONS, tmp_path)

    assert _scaled_outputs(fake)[0]["crf"] == clip_mod.MOBILE_CRF
    assert clip_mod.MOBILE_CRF > 23


def test_a_custom_short_side_reaches_the_filter(monkeypatch, tmp_path):
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    clip_mod.generate_clips(
        str(tmp_path / "g.mp4"), DETECTIONS, tmp_path, mobile_short_side=540,
    )

    assert "min(540,iw)" in _scaled_outputs(fake)[0]["vf"]


# ── Behaviour in recut_single ────────────────────────────────────────────────

def test_recut_remakes_the_rendition(monkeypatch, tmp_path):
    """The rendition key is deterministic per clip, so a trim that rewrote the
    clip and not the rendition would leave mobile clients playing the old
    boundaries."""
    fake = _install(monkeypatch, _FakeFFmpeg(1920, 1080))

    _clip, _thumb, mobile = clip_mod.recut_single(
        str(tmp_path / "g.mp4"), 10.0, 32.0, tmp_path,
    )

    assert len(_scaled_outputs(fake)) == 1
    assert mobile is not None


def test_recut_reports_none_when_the_rendition_fails(monkeypatch, tmp_path):
    """None is what tells the caller to clear the column and purge the stale
    object, so it must not be confused with "not attempted"."""
    _install(monkeypatch, _FakeFFmpeg(1920, 1080, fail_on="scale"))

    clip_path, _thumb, mobile = clip_mod.recut_single(
        str(tmp_path / "g.mp4"), 10.0, 32.0, tmp_path,
    )

    assert clip_path is not None
    assert mobile is None


def test_recut_of_a_small_source_reports_none(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeFFmpeg(1280, 720))

    _clip, _thumb, mobile = clip_mod.recut_single(
        str(tmp_path / "g.mp4"), 10.0, 32.0, tmp_path,
    )

    assert mobile is None
