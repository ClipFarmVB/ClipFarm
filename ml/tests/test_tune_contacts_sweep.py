"""
CF-309: a sweep row that scores the shipping defaults is a second baseline.

`tune_contacts.py` prints `BASELINE (shipping defaults)` and then a row per
swept value. A swept value that *equals* the current default re-scores the
baseline under a knob's name — two identical rows, one of them claiming to show
what changing the knob does.

It drifts from the other end, which is why it needs a test rather than care:
the sweep held `CONTACT_RESIDUAL_MIN_PXPS=240` from when `ball.py` shipped 480,
and CF-103 moving the default to 240 turned that row into a duplicate without
touching `tune_contacts.py` at all. Nothing said so; the tuner kept printing a
table with a hidden repeat in it for the whole of that time.

`ball` is imported for the defaults — it reaches cv2 only lazily, so numpy is
enough, exactly as `test_ball_scaling.py` records. The sweep tables are *parsed*
rather than imported, following `test_eval_condense_settings.py`: importing
`tune_contacts` pulls in `ml.eval.harness` and `ml.eval.metrics` as well, and
this check needs four literals, not a working eval stack.
"""
import ast
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline import ball as B  # noqa: E402

TUNE_PY = Path(__file__).resolve().parents[2] / "ml" / "eval" / "tune_contacts.py"


def _literal(name: str):
    """The module-level literal assigned to `name`, without importing."""
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign)
            else node.targets if isinstance(node, ast.Assign)
            else []
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found as a module-level literal in {TUNE_PY.name}")


SWEEPS = _literal("SWEEPS")
COMBOS = _literal("COMBOS")
TUNABLES = _literal("TUNABLES")


def _combos():
    """`COMBOS` as (label, overrides), tolerating the tuple-of-pairs shape."""
    return [(label, dict(overrides)) for label, overrides in COMBOS]


def test_the_tables_were_actually_found():
    """A parse that silently matched nothing would make every test below
    vacuous — the same guard the fixture suites carry, for the same reason."""
    assert SWEEPS, "no SWEEPS table parsed"
    assert _combos(), "no COMBOS table parsed"
    assert len(TUNABLES) >= 6, TUNABLES


@pytest.mark.parametrize("name", sorted(SWEEPS))
def test_no_swept_value_repeats_the_shipping_default(name):
    default = getattr(B, name)
    assert default not in SWEEPS[name], (
        f"{name} sweeps {SWEEPS[name]} and ball.py ships {default!r}, so that "
        f"row re-scores BASELINE under another name. Drop the value — the "
        f"baseline row already reports it."
    )


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

