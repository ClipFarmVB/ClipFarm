"""The dev set has to actually be installed, or guarded tests pass by skipping.

Deliberately its own module with **no module-level `importorskip`**. The first
home for this test was `test_pose_modal.py`, which opens with
`pytest.importorskip("celery")` — so the test guarding against silent skips
could itself vanish into a green run, in precisely the way it exists to catch.
A review round caught that. Anything added here must keep this file importable
with nothing beyond the standard library and pytest itself — which also covers
the pre-commit gate script under test below, itself stdlib-only.

This file is NOT excluded from its own scan, and no longer needs to be: the
scan below parses the AST, where a docstring is a string constant and can
never be a call, so quoting the call as prose above is structurally invisible
to it. Two earlier revisions defended that by punctuation instead — first by
excluding this file by path, then by relying on the backticks around the quote
to keep it off a line-anchored regex — and both exempted, or nearly exempted,
the one module documented as needing to stay guard-free. The assertion that
this module contributes no targets is kept below, and under the AST scan it
finally means what it says: not "the docstring was not reflowed", but "no
guard was added to the guard-checker".
"""
import ast
import importlib
import pathlib
import typing

import pytest

# Plain import, on the same footing as every `from app...` in this suite: both
# rely on api/ being on sys.path, which `python -m pytest` (README, ci.yml)
# provides. It is also the point of the script existing — one importable
# definition of how a requirements line is read, shared with test_pose_modal.py
# instead of copied into it.
from scripts import check_dev_set

TESTS_DIR = pathlib.Path(__file__).resolve().parent


class Scan(typing.NamedTuple):
    """What one module contributes to the guard scan."""

    targets: set  # string literals passed to importorskip
    dynamic: list  # call sites whose target is not a string literal


def importorskip_targets(path):
    """Find every `importorskip` call in `path` by parsing it (CF-279).

    A regex has to choose between two failure modes and this scan was bitten by
    both. A loose pattern matched a call quoted as prose in a docstring — this
    module's own — and invented a dependency out of the file's explanation of
    itself. The anchored pattern that fixed that then stopped seeing four real
    forms: a call split across lines, a call not at the start of a line, an
    assignment to a subscript, and the `from pytest import importorskip` alias.

    In the AST both modes go at once rather than being traded: prose is a
    `Constant` and can never be a `Call`, and position stops mattering because
    there are no lines to anchor to.

    Two alias forms are deliberately still out of scope, because resolving them
    means tracking import bindings and nothing in this suite uses them:
    `import pytest as pt` followed by `pt.importorskip(...)`, and
    `from pytest import importorskip as ios`. They are named here so the next
    reader can tell a decision from an oversight.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(
            f"{path} does not parse, so it cannot be scanned for guarded "
            f"imports: {exc}"
        ) from exc

    targets, dynamic = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            isinstance(func, ast.Attribute)
            and func.attr == "importorskip"
            and isinstance(func.value, ast.Name)
            and func.value.id == "pytest"
        ) or (isinstance(func, ast.Name) and func.id == "importorskip")
        if not called:
            continue
        # `modname` is positional-or-keyword in pytest's signature; only
        # `exc_type` is keyword-only. Both spellings have to be read.
        arg = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == "modname"), None
        )
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            targets.add(arg.value)
        else:
            # A computed target cannot be checked against the environment, so
            # it would leave the suite exactly as unguarded as the call forms
            # this rewrite exists to stop missing — silently. Collected rather
            # than dropped, and asserted against below.
            dynamic.append(f"{path.name}:{node.lineno}")
    return Scan(targets, dynamic)


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
    dynamic = []
    # rglob, not glob: a guard in a future subdirectory of api/tests would
    # otherwise escape the check silently, which is this test's whole subject.
    for path in sorted(TESTS_DIR.rglob("*.py")):
        scan = importorskip_targets(path)
        targets |= scan.targets
        dynamic += scan.dynamic

    assert not dynamic, (
        f"{dynamic} guard on a target this scan cannot read, because it is not "
        "a string literal. Such a call skips its test on a missing dependency "
        "exactly like any other, but nothing here can check it against the "
        "environment — which is the silent hole this scan exists to close. "
        "Pass the module name as a literal."
    )

    # This module must stay guard-free: a `importorskip` here could skip the
    # test that catches silent skips, which is the failure that put this test
    # in its own file. Asserting it against the scan rather than against
    # punctuation is what the AST rewrite bought — the previous version of this
    # assertion could only fail if the docstring above had been reflowed.
    assert not importorskip_targets(pathlib.Path(__file__)).targets, (
        "this module now contains an importorskip call — it must stay "
        "importable with stdlib and pytest alone, or the guard-checker can "
        "itself vanish into a green run (see this module's docstring)"
    )

    # First-party `app.*` targets are excluded on purpose. They are not what can
    # be missing — they ship with the repo — and importing them is not free:
    # `app/config.py` instantiates `Settings()` at module scope, so scanning it
    # here would run application configuration as a side effect of a
    # dependency check. The failure this guards is a third-party package absent
    # from requirements-dev.txt.
    third_party = sorted(t for t in targets if not t.startswith("app."))
    # A floor, not merely non-empty. The risk this rewrite carries is a
    # *partial* shrink — a scan that still finds something, so a non-empty
    # check stays green, while quietly dropping most of the suite's guards.
    # 13 today; the same shape as `len(pins) >= 15` in the gate test below.
    assert len(third_party) >= 10, (
        f"only {len(third_party)} third-party importorskip targets found "
        f"({third_party}) — did this suite's layout or the scan change?"
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


# ── The scan itself ──────────────────────────────────────────────────────────
# The scan is what decides whether the test above can see a guard at all, so a
# form it misses is a hole with no symptom. One fixture exhibits both regex
# failure modes at once, measured against `FORMS` below (CF-279):
#
#   loose pattern     7 targets — invents `phantom` from the docstring and
#                     `commented` from a comment; still misses `keyword`
#   anchored pattern  2 targets — `plain` and `wrapped` only; blind to
#                     `inline`, `subscript`, `aliased` and `keyword`
#   this AST scan     6 targets — the six real calls, and nothing else
#
# So `plain` and `wrapped` are the controls rather than the subjects: they are
# the two forms the anchored pattern did see.

FORMS = '''
"""A docstring that quotes pytest.importorskip("phantom") as prose."""
import pytest
from pytest import importorskip

plain = pytest.importorskip("plain")

wrapped = pytest.importorskip(
    "wrapped"
)

if True: pytest.importorskip("inline")

bucket = {}
bucket["k"] = pytest.importorskip("subscript")

aliased = importorskip("aliased")

kw = pytest.importorskip(modname="keyword")

# pytest.importorskip("commented") — a mention in a comment is not a call.
'''


def test_the_scan_finds_every_call_form(tmp_path):
    """Four of these six were invisible to the regex this replaced; `plain`
    and `wrapped` are the controls it did see."""
    f = tmp_path / "forms.py"
    f.write_text(FORMS, encoding="utf-8")
    assert importorskip_targets(f).targets == {
        "plain", "wrapped", "inline", "subscript", "aliased", "keyword",
    }


def test_the_scan_reads_no_call_out_of_prose(tmp_path):
    """The other direction, and the one that bit first: a loose pattern
    invented a dependency out of a docstring quoting the call as an example."""
    f = tmp_path / "prose.py"
    f.write_text(FORMS, encoding="utf-8")
    found = importorskip_targets(f).targets
    assert "phantom" not in found, "a quoted call in a docstring became a target"
    assert "commented" not in found, "a call in a comment became a target"


def test_the_scan_reports_a_target_it_cannot_read(tmp_path):
    """A computed target is not a target this scan can check, and saying so is
    the difference between a known gap and a silent one."""
    f = tmp_path / "dynamic.py"
    f.write_text(
        'import pytest\nname = "x"\npytest.importorskip(name)\n', encoding="utf-8"
    )
    scan = importorskip_targets(f)
    assert scan.targets == set()
    assert scan.dynamic == ["dynamic.py:3"]


def test_the_scan_names_a_file_it_cannot_parse(tmp_path):
    """Without this the failure surfaces as a bare SyntaxError from ast.parse,
    which says nothing about why this test was reading the file."""
    f = tmp_path / "broken.py"
    f.write_text("def (\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="does not parse"):
        importorskip_targets(f)


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
