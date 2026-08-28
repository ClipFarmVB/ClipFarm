"""The dev set has to actually be installed, or guarded tests pass by skipping.

Deliberately its own module with **no module-level `importorskip`**. The first
home for this test was `test_pose_modal.py`, which opens with
`pytest.importorskip("celery")` — so the test guarding against silent skips
could itself vanish into a green run, in precisely the way it exists to catch.
A review round caught that. Anything added here must keep this file importable
with nothing beyond the standard library and pytest itself — which also covers
the pre-commit gate script under test below, itself stdlib-only.

This file is NOT excluded from its own scan, so the backticks around the
quoted call above are load-bearing: they keep the line from matching the
line-anchored pattern below. An earlier revision excluded the file by path
instead, which was dead code (the backticks already prevented the match) and
exempted forever the one module documented as needing to stay guard-free.
"""
import importlib
import pathlib
import re

import pytest

# Plain import, on the same footing as every `from app...` in this suite: both
# rely on api/ being on sys.path, which `python -m pytest` (README, ci.yml)
# provides. It is also the point of the script existing — one importable
# definition of how a requirements line is read, shared with test_pose_modal.py
# instead of copied into it.
from scripts import check_dev_set

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
    per_file = {}
    # rglob, not glob: a guard in a future subdirectory of api/tests would
    # otherwise escape the check silently, which is this test's whole subject.
    for path in sorted(TESTS_DIR.rglob("*.py")):
        # Anchored to the start of a line, with an optional assignment, so a
        # match has to look like code rather than merely appear in the file —
        # a docstring quoting the call as prose (as this module's own does,
        # behind a backtick) would otherwise inject a phantom dependency into
        # the check. `\(\s*` lets the quoted target sit on the next line for a
        # wrapped call; two calls on one line would evade the anchor, but ruff
        # (E702, in the hook and CI) forbids the `;` that takes.
        for m in re.finditer(
            r"""^[ \t]*(?:[\w, ]+=[ \t]*)?pytest\.importorskip\(\s*["']([^"']+)["']""",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        ):
            targets.add(m.group(1))
            per_file.setdefault(path.name, set()).add(m.group(1))

    # This module's docstring quotes an importorskip call as prose, and stays
    # out of the scan only because that quote sits behind a backtick. Asserting
    # it here puts that fact in code rather than in punctuation: reflow the
    # docstring so the quote starts a line and this fails, instead of a phantom
    # dependency appearing in the list below.
    assert pathlib.Path(__file__).name not in per_file, (
        f"this module now contributes scan targets ({per_file[pathlib.Path(__file__).name]}) "
        "— its docstring quotes the call as prose, so a reflow has made prose "
        "look like code; re-inline the quote behind a backtick"
    )

    # First-party `app.*` targets are excluded on purpose. They are not what can
    # be missing — they ship with the repo — and importing them is not free:
    # `app/config.py` instantiates `Settings()` at module scope, so scanning it
    # here would run application configuration as a side effect of a
    # dependency check. The failure this guards is a third-party package absent
    # from requirements-dev.txt.
    third_party = sorted(t for t in targets if not t.startswith("app."))
    assert third_party, (
        "no third-party importorskip targets found — did this suite's layout "
        "or the scan pattern change?"
    )

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


# ── The pre-commit gate script ───────────────────────────────────────────────
# api/scripts/check_dev_set.py decides whether the hook runs this very suite.
# Its predecessor — a probe of one chosen package — could never succeed after
# CF-184 and skipped the step on every correct dev install for years, so the
# replacement does not get to be untested.


def test_gate_follows_every_include_spelling(tmp_path):
    """Dropping an include is how the gate stopped checking app deps before."""
    (tmp_path / "base.txt").write_text("fastapi==0.1\n", encoding="utf-8")
    for spelling in ("-r base.txt", "-rbase.txt", "--requirement base.txt",
                     "--requirement=base.txt"):
        f = tmp_path / "reqs.txt"
        f.write_text(f"{spelling}\npytest==8.0\n", encoding="utf-8")
        pins = check_dev_set.parse(f, set())
        assert pins == {"fastapi": "0.1", "pytest": "8.0"}, spelling


def test_gate_reads_pip_legal_pin_spellings(tmp_path):
    """Whitespace around `==` and an extras spec are both pip-legal; a pin
    written either way must not silently leave the checked set."""
    f = tmp_path / "reqs.txt"
    f.write_text(
        "fastapi == 0.1\nuvicorn[standard]==0.2  # comment\nscipy>=1.0\n",
        encoding="utf-8",
    )
    pins = check_dev_set.parse(f, set())
    assert pins == {"fastapi": "0.1", "uvicorn": "0.2"}
    # scipy>=1.0 is absent by documented design: ranges are skipped, not pins.


def test_gate_own_pins_beat_included_ones_wherever_the_include_sits(tmp_path):
    (tmp_path / "base.txt").write_text("fastapi==0.1\n", encoding="utf-8")
    for layout in ("-r base.txt\nfastapi==0.2\n", "fastapi==0.2\n-r base.txt\n"):
        f = tmp_path / "reqs.txt"
        f.write_text(layout, encoding="utf-8")
        assert check_dev_set.parse(f, set()) == {"fastapi": "0.2"}, layout


def test_gate_names_a_missing_include_instead_of_crashing(tmp_path, capsys):
    f = tmp_path / "reqs.txt"
    f.write_text("-r nope.txt\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        check_dev_set.parse(f, set())
    assert "missing requirements file" in capsys.readouterr().out


def test_gate_parses_the_real_dev_set():
    """Binds the parser to the actual files — and keeps the numpy pin a plain
    `numpy==X` line in requirements-dev.txt. Loosening it to a range or hiding
    it behind an environment marker drops it from the gate *and* from the
    pin-consistency test's regex at once (both match only `==`), so this is
    the assertion that makes that loosening loud rather than silently green
    (CF-276)."""
    pins = check_dev_set.parse(
        check_dev_set.API_DIR / "requirements-dev.txt", set()
    )
    assert "numpy" in pins, (
        "requirements-dev.txt no longer carries a plain `numpy==X` pin — the "
        "CF-276 guard now tests whichever numpy pip happens to resolve"
    )
    assert "fastapi" in pins, "the `-r requirements.txt` include was not followed"
    assert len(pins) >= 15, f"suspiciously few pins parsed: {sorted(pins)}"


def test_gate_passes_in_an_environment_built_from_the_dev_file():
    """In CI (and any correct dev install) main() must return 0 — this is also
    the check that the file's distribution names resolve via importlib.metadata
    (PyYAML, psycopg2-binary), not just as import names."""
    assert check_dev_set.main() == 0
