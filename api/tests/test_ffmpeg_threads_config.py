"""CF-224 — FFMPEG_THREADS must be a real bound, checked at boot.

ffmpeg reads the visible core count, which in a container is the host's, so an
unpinned x264 OOM-killed the 512 MB worker. The setting that pins it has one
value that undoes it silently: 0 is ffmpeg's *auto* sentinel, not "no threads",
and restores the host-sized pool with nothing in the log to say so. A negative
fails every cut inside the pipeline's swallowing except, producing a game with
no clips. Both belong at startup, next to the plan they are sized against.
"""

import pytest

pytest.importorskip("pydantic_settings")

from pydantic import ValidationError  # noqa: E402

from app.config import Settings  # noqa: E402


def test_default_is_a_bound():
    assert Settings(_env_file=None).ffmpeg_threads >= 1


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_value_that_is_not_a_bound_is_refused(value, monkeypatch):
    monkeypatch.setenv("FFMPEG_THREADS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_real_thread_count_is_accepted(monkeypatch):
    monkeypatch.setenv("FFMPEG_THREADS", "4")

    assert Settings(_env_file=None).ffmpeg_threads == 4
