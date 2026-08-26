"""The dev set has to actually be installed, or guarded tests pass by skipping.

Deliberately its own module with **no module-level `importorskip`**. The first
home for this test was `test_pose_modal.py`, which opens with
`pytest.importorskip("celery")` — so the test guarding against silent skips
could itself vanish into a green run, in precisely the way it exists to catch.
A review round caught that. Anything added here must keep this file importable
with nothing but the standard library.
"""
import importlib
import pathlib
import re

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def test_every_importorskip_target_is_installed():
    """No test in this suite may skip because a dependency is missing (CF-276).

    This is the general form of the bug that card was about, and it would have
    caught it: `test_numpy_scalars_never_reach_the_modal_boundary` guards on
    numpy, numpy was in neither requirements file, and so that test skipped in
    CI from the day it was written — reported as a pass, forever.

    Asserting against the *environment* rather than against a requirements file
    is the point. In CI the environment is built from `requirements-dev.txt`, so
    this holds that file to every guard in the suite, in both directions: adding
    a guarded import without the dependency fails here instead of skipping
    silently, and deleting a dependency something guards on fails here too.

    `importorskip` stays on the individual tests as belt-and-braces — this test
    is the belt, not a licence to remove them.
    """
    targets = set()
    for path in sorted(TESTS_DIR.glob("*.py")):
        for m in re.finditer(
            r"""importorskip\(\s*["']([^"']+)["']""", path.read_text(encoding="utf-8")
        ):
            targets.add(m.group(1))

    assert targets, "no importorskip targets found — did this suite's layout change?"

    # First-party `app.*` targets are excluded on purpose. They are not what can
    # be missing — they ship with the repo — and importing them is not free:
    # `app/config.py` instantiates `Settings()` at module scope, so scanning it
    # here would run application configuration as a side effect of a
    # dependency check. The failure this guards is a third-party package absent
    # from requirements-dev.txt.
    third_party = sorted(t for t in targets if not t.startswith("app."))
    assert third_party, "only first-party targets found — the filter is too broad"

    missing = []
    for name in third_party:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    assert not missing, (
        f"{missing} are guarded by importorskip but not installed, so the tests "
        "behind them skip and are counted as passes. Add them to "
        "api/requirements-dev.txt — which is the file the api CI job installs."
    )
