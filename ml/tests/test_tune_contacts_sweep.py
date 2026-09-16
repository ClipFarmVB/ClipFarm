"""
CF-309: a sweep row that scores the shipping defaults is a second baseline.

`tune_contacts.py` prints `BASELINE (shipping defaults)` and then a row per
swept value. A swept value that *equals* the current default re-scores the
baseline under a knob's name — two identical rows, one of them claiming to show
what changing the knob does.

It drifts from the other end, which is why it needs a test rather than care:
the sweep held `CONTACT_RESIDUAL_MIN_PXPS=240` from when `ball.py` shipped 480,
and CF-103 (`55c6b21`) moving the default to 240 turned that row into a
duplicate without touching `tune_contacts.py` at all. Nothing said so; the
tuner kept printing a table with a hidden repeat in it for the whole of that
time.

**The tables are IMPORTED, not parsed.** An earlier version read them out of
the source with `ast.literal_eval`, following `test_eval_condense_settings.py`.
That was wrong here, and wrong in the way this file is about: it asserted
properties of the file's *literals* while the tuner runs the file's
*bindings*, so `SWEEPS.update(...)`, `SWEEPS[k] = ...` or `COMBOS += ...`
anywhere below the literal left every assertion green about a table the tuner
does not use — reintroducing the duplicate row this file exists to forbid.
Fixing the one spelling a round happened to name (a second `SWEEPS = ...`) left
its neighbours, which is the same mistake one level down.

Importing is safe here and the precedent is not: `ml.pipeline.ball` reaches cv2
only lazily (`test_ball_scaling.py` records this), `ml.eval.harness` and
`ml.eval.metrics` are stdlib plus numpy, and `ci.yml` installs numpy before
`pytest ml/tests/`.

The parse survives for two jobs, both about the SOURCE rather than the values:
checking that it reads as what Python binds, and catching a key written twice
in the table — which equality cannot find, since Python and `literal_eval` both
keep the last and the values therefore agree while the file reads as sweeping
something it does not.

And none of this reaches `_sweep`, which is free to ignore both tables and
re-type a literal loop. That rung is three tests in
`test_tune_contacts_fixture.py`: one compares every row the tuner prints
against these tables in both directions, one pins the order, and one records
the ball constants as `find_contacts` sees them and checks each row scored the
value its label advertises. Rows are found by the SHAPE of `_row`'s output
rather than by a list of label prefixes, because a prefix list is an
enumeration of today's spellings — which is the mistake this file is about.
"""
import ast
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import tune_contacts as TC  # noqa: E402
from ml.pipeline import ball as B  # noqa: E402

TUNE_PY = Path(__file__).resolve().parents[2] / "ml" / "eval" / "tune_contacts.py"

SWEEPS = TC.SWEEPS
COMBOS = TC.COMBOS
TUNABLES = TC.TUNABLES


def _literal(name: str):
    """The module-level literal assigned to `name`, read from the source.

    Used only by `test_the_source_reads_as_what_python_binds`. The LAST
    assignment wins, because that is the one Python leaves bound.
    """
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    found = None
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign)
            else node.targets if isinstance(node, ast.Assign)
            else []
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                found = node.value
    if found is None:
        raise AssertionError(
            f"{name} not found as a module-level literal in {TUNE_PY.name}")
    try:
        return ast.literal_eval(found)
    except ValueError as exc:
        raise AssertionError(
            f"{TUNE_PY.name}: `{name}` is not a literal ({exc}). The table is "
            f"meant to read as a table — write the value out rather than "
            f"computing it."
        ) from exc


def _duplicate_literal_keys(name: str) -> list[str]:
    """Keys written twice in `name`'s dict literal.

    Python keeps the last and so does `literal_eval`, so this cannot be found by
    comparing values — the runtime table is correct while the source reads as
    sweeping something it does not.
    """
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign)
            else node.targets if isinstance(node, ast.Assign)
            else []
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and isinstance(node.value, ast.Dict):
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                return sorted({k for k in keys if keys.count(k) > 1})
    return []


def _combos():
    """`COMBOS` as (label, overrides), tolerating the tuple-of-pairs shape."""
    return [(label, dict(overrides)) for label, overrides in COMBOS]


@pytest.mark.parametrize("name", ["SWEEPS", "COMBOS", "TUNABLES"])
def test_the_source_reads_as_what_python_binds(name):
    """The one job the parse still has, and the reason the rest of this file
    imports instead.

    Every assertion below reads the IMPORTED table, so it is always true of what
    the tuner runs. That leaves one gap in the other direction: a module-level
    rebinding — `+=`, `SWEEPS[k] = ...`, `.update(...)` — makes the literal a
    reader sees and the table the tuner uses two different things, and nothing
    that looks only at one of them can tell. Compared rather than trusted.
    """
    assert _literal(name) == getattr(TC, name), (
        f"{name}'s literal in {TUNE_PY.name} is not what the module ends up "
        f"bound to — something rebinds it below the table. Whichever is right, "
        f"a reader of the file and the tuner currently disagree."
    )


@pytest.mark.parametrize("name", ["SWEEPS"])
def test_no_key_is_written_twice_in_the_table(name):
    """Python keeps the last, so the values agree and only the source lies:
    the file reads as sweeping a knob at values it does not sweep."""
    dupes = _duplicate_literal_keys(name)
    assert not dupes, f"{name} lists {dupes} more than once; only the last counts"


def test_the_tables_were_actually_found():
    """An empty table would make every test below vacuous — the same guard the
    fixture suites carry, for the same reason. Cheap now that the tables are
    imported rather than parsed, and kept because "vacuously green" is the
    failure mode, not "parsed wrongly"."""
    assert SWEEPS, "SWEEPS is empty"
    assert _combos(), "COMBOS is empty"
    assert len(TUNABLES) >= 6, TUNABLES


@pytest.mark.parametrize("name", sorted(SWEEPS))
def test_no_swept_value_repeats_the_shipping_default(name):
    default = getattr(B, name)
    assert default not in SWEEPS[name], (
        f"{name} sweeps {SWEEPS[name]} and ball.py ships {default!r}, so that "
        f"row re-scores BASELINE under another name. Drop the value — the "
        f"baseline row already reports it."
    )


@pytest.mark.parametrize("name", sorted(SWEEPS))
def test_no_knob_is_swept_twice_at_the_same_value(name):
    """A repeated value prints the same row twice, which is this file's whole
    subject arriving from the other direction: the duplicate that started CF-309
    was a value equal to the *default*, but a value equal to its *neighbour* is
    the same table with the same hidden repeat in it."""
    values = SWEEPS[name]
    dupes = sorted({v for v in values if list(values).count(v) > 1})
    assert not dupes, f"{name} sweeps {dupes} more than once in {values}"


@pytest.mark.parametrize("name", sorted(SWEEPS))
def test_no_knob_has_an_empty_sweep(name):
    """`set(SWEEPS)` being pinned does not stop a knob's tuple being emptied,
    and an empty tuple stops the knob being explored while every other check
    stays green — the same observable outcome as deleting the key, which the
    pinned set does catch."""
    assert SWEEPS[name], f"{name} is in SWEEPS but sweeps nothing"


def test_no_two_combos_are_the_same_run():
    """Identical override sets print identical rows under different labels, and
    identical labels make two different rows indistinguishable in the table.
    Both are the defect this file exists for, one level up from the sweeps."""
    combos = _combos()
    # Normalised, because the table is read by eye: "combo: X" and "Combo:  X"
    # are one row to anyone looking at it, and two to `set()`.
    labels = [" ".join(label.split()).casefold() for label, _ in combos]
    assert len(set(labels)) == len(labels), (
        f"combo labels collide once whitespace and case are normalised: "
        f"{[label for label, _ in combos]}"
    )
    seen: list[tuple[str, dict]] = []
    for label, overrides in combos:
        clash = [prev for prev, o in seen if o == overrides]
        assert not clash, f"{label!r} scores exactly what {clash[0]!r} scores"
        seen.append((label, overrides))


def test_the_combos_are_written_cumulatively():
    """`_sweep` bases stage 2 on `COMBOS[-1]`, so "last" has to mean "fullest".

    That was a comment and nothing else: reordering the two entries re-based the
    padding sweep on a smaller combo, silently, and the whole suite stayed green
    — a comment claiming a property nothing held, which is the defect this PR
    has now produced four times. Each entry being a superset of the one before
    it makes `[-1]` mean what the comment says.
    """
    previous: dict = {}
    for label, overrides in _combos():
        missing = {k: v for k, v in previous.items() if overrides.get(k) != v}
        assert not missing, (
            f"{label!r} drops {sorted(missing)} from the combo before it, so "
            f"COMBOS[-1] is no longer the fullest and stage 2 sweeps padding "
            f"on top of something narrower than the table reads"
        )
        previous = overrides


@pytest.mark.parametrize("label,overrides", _combos(), ids=[c[0] for c in _combos()])
def test_no_combo_pins_a_knob_to_its_shipping_default(label, overrides):
    """`score()` applies `overrides.get(k, default)`, so pinning the default is
    a no-op — and a misleading one: every combo here once carried
    `CONTACT_RESIDUAL_MIN_PXPS=240.0`, which stopped meaning anything the day
    240 became the default, while the labels went on advertising it."""
    noops = {k: v for k, v in overrides.items() if getattr(B, k) == v}
    assert not noops, (
        f"{label!r} pins {noops} at the shipping default, which `score()` "
        f"applies as no change at all. Drop it from the override set and from "
        f"the label."
    )


@pytest.mark.parametrize("label,overrides", _combos(), ids=[c[0] for c in _combos()])
def test_no_combo_collapses_onto_a_single_knob_row(label, overrides):
    """With the no-op pins gone, a "combo" of one knob is just that knob's
    sweep row printed twice. This is how `combo: resid 240 + hit 120` became an
    alias for `CONTACT_HIT_SPEED_PXPS=120`."""
    assert len(overrides) >= 2, (
        f"{label!r} varies only {sorted(overrides)}, which the single-knob "
        f"sweep above already covers"
    )


@pytest.mark.parametrize("label,overrides", _combos(), ids=[c[0] for c in _combos()])
def test_every_swept_knob_is_restored_after_scoring(label, overrides):
    """`score()` restores exactly `TUNABLES`, so an override outside that tuple
    is set on the module and never put back — every row after it is scored
    against a mutated `ball`, silently, and the table stays plausible."""
    stray = sorted(set(overrides) - set(TUNABLES))
    assert not stray, f"{label!r} overrides {stray}, which score() does not restore"


def test_every_swept_knob_is_a_declared_tunable():
    stray = sorted(set(SWEEPS) - set(TUNABLES))
    assert not stray, f"SWEEPS covers {stray}, which score() does not restore"


def test_the_swept_knobs_are_the_ones_intended():
    """Pinned as a set, so dropping a knob from the sweep is a diff rather than
    a table that quietly stops exploring it.

    Not `== TUNABLES`: `SEG_MAX_SPEED_PXPS` and `MAX_SAMPLE_GAP_SEC` are
    deliberately unswept — the first feeds `_scale_for`'s cap, so a row moving
    it shifts the clamp underneath itself, which the module docstring records
    as the CF-174 trap. If a sweep is genuinely added or retired, update this
    set in the same commit and the diff carries the decision.
    """
    assert set(SWEEPS) == {
        "CONTACT_RESIDUAL_MIN_PXPS", "CONTACT_RESIDUAL_RATIO",
        "CONTACT_HIT_SPEED_PXPS", "SEG_MIN_POSITIONS",
        "SEG_MIN_MEDIAN_SPEED_PXPS", "MIN_CONTACT_SPACING",
    }, sorted(SWEEPS)

