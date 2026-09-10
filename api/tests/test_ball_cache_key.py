"""The ball cache key carries everything that shapes the track (CF-231, #238).

Cached tracks live in R2 under a key built from the video, the model and the
sample rate. Anything else that changes what tracking produces was invisible to
it, so the next run of an already-processed video replayed a track built by the
old code — a wrong answer nobody can see on a tuning change, and worse on a
re-measure, where unchanged numbers read as "no effect" rather than "it never
ran". Both #238 and #233 name this as their prerequisite.

`ml/tests/test_ball_cache_version.py` guards the other half: that the version
moves when a track-shaping constant does. This guards that the version is in
the key at all, which is the half that lives on the api side.
"""
import pytest

pytest.importorskip("celery")
pytest.importorskip("numpy")

# Import order matters here and is not obvious. Run from `api/`, `ml` is not on
# sys.path — `app/workers/celery_app.py` is what inserts the project root, and
# `app.workers.tasks` imports it. So the app import has to come FIRST, and an
# `importorskip("ml.pipeline.ball")` above it would skip this whole file,
# silently and forever. `test_condense_windows.py` orders it the same way.
from app.workers.tasks import _ball_cache_key  # noqa: E402
from ml.pipeline.ball import MODEL_ID, TRACKING_CACHE_VERSION  # noqa: E402

MD5 = "0123456789abcdef0123456789abcdef"


def test_the_key_carries_the_tracking_version():
    assert f"-v{TRACKING_CACHE_VERSION}" in _ball_cache_key(MD5, 3)


def test_a_version_bump_changes_the_key():
    """The whole point: after a bump, yesterday's entry must not be found.

    Compared against a rebuilt key rather than a hardcoded string, so this
    keeps testing the property after the next bump rather than the value it had
    when it was written.
    """
    import app.workers.tasks as tasks

    before = _ball_cache_key(MD5, 3)
    import ml.pipeline.ball as ball_module

    original = ball_module.TRACKING_CACHE_VERSION
    try:
        ball_module.TRACKING_CACHE_VERSION = original + 1
        after = tasks._ball_cache_key(MD5, 3)
    finally:
        ball_module.TRACKING_CACHE_VERSION = original

    assert before != after, "a bumped version must not resolve to the same object"


def test_the_key_still_separates_videos_models_and_sample_rates():
    # The three components that were already there. Adding the fourth must not
    # have collapsed any of them.
    base = _ball_cache_key(MD5, 3)
    assert base != _ball_cache_key("f" * 32, 3), "different video"
    assert base != _ball_cache_key(MD5, 10), "different sample rate"
    assert MODEL_ID.replace("/", "-") in base, "different model"


def test_the_key_is_a_plain_r2_path_with_no_separator_collision():
    """`v10` must not be reachable from `v1` plus a stray character.

    The components are joined with `-`, and a model slug is the one part that
    could contain one. It is fenced by the `-s{n}-v{n}.json` tail, so this
    asserts the shape rather than trusting it.
    """
    key = _ball_cache_key(MD5, 3)
    assert key.startswith("ball-cache/")
    assert key.endswith(f"-s3-v{TRACKING_CACHE_VERSION}.json")
    assert key.count(".json") == 1
