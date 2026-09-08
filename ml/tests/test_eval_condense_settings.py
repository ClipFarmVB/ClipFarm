"""
Guards the one place the eval tooling copies production condense settings.

`harness.py --offline` reads the condense knobs from `app.config.settings`, the
same object `process_game_task` reads, so those two cannot drift. `tune_contacts.py`
cannot: it runs on a dumped ball track with no app installed, so it hardcodes the
values "as recorded in the baseline result row".

That second copy is the risk. If production's defaults move and the constants
here don't, every sweep the tuner reports is scored against settings the
pipeline no longer uses — and it fails silently, producing numbers that look
entirely reasonable.

Parsed rather than imported: these tests run before the app's dependencies are
installed in CI, and `tune_contacts` pulls in `ml.pipeline.ball`.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
CONFIG_PY = REPO / "api" / "app" / "config.py"
TUNE_PY = REPO / "ml" / "eval" / "tune_contacts.py"
# Every eval module that consumes the NORMALIZE mirror. deadtime_variants
# imports it from tune_contacts, so a check that scanned only the file the
# constant is *declared* in could not see the consumer that drops it — which
# is exactly what happened: v0_shipped, the baseline every v1..v5 is scored
# against, passed it to find_contacts and not to the bridge.
MIRROR_CONSUMERS = (
    TUNE_PY,
    REPO / "ml" / "eval" / "deadtime_variants.py",
)
DEAD_TIME_PY = REPO / "ml" / "pipeline" / "dead_time.py"

# tune_contacts constant -> the app.config setting it copies.
COND_TO_SETTING = {
    "gap_seconds": "condense_gap_seconds",
    "pad_before": "condense_pad_before",
    "pad_after": "condense_pad_after",
    "min_contacts": "condense_min_contacts",
    "merge_gap_seconds": "condense_merge_gap_seconds",
}
BRIDGE_TO_SETTING = {
    "speed_pxps": "condense_bridge_speed_pxps",
    "fast_fraction": "condense_bridge_fast_fraction",
    "max_bridge_seconds": "condense_bridge_max_seconds",
}
# app.config setting -> the active_windows_guarded() kwarg it feeds. The guarded
# path (CF-187) is the default, so these are the numbers production runs; the
# function's defaults are what deadtime_variants.v5 scores in the eval harness,
# and the two must agree or the comparison stops describing production.
GUARD_TO_KWARG = {
    "condense_guard_gate_speed": "gate_speed",
    "condense_guard_anchor_speed": "anchor_speed",
    "condense_guard_pad_before": "pad_before",
    "condense_guard_pad_after": "pad_after",
    "condense_guard_merge_gap_seconds": "merge_gap_seconds",
    "condense_guard_min_track_rate": "min_track_rate",
}
# Mode switches, not tunables: not copied into tune_contacts, which sweeps the
# rule-based path only. Listed here so the coverage test stays a conscious
# checkpoint for every new condense_* knob.
SWITCH_SETTINGS = {"condense_mode"}

# Settings outside the condense_ prefix that the eval tooling still copies, so
# the coverage test above cannot see them. `ball_contact_scale_enabled` (CF-174)
# is mirrored as tune_contacts.NORMALIZE and reaches both find_contacts and the
# motion bridge, which is why drift here would move every swept number.
NON_CONDENSE_TO_MIRROR = {"ball_contact_scale_enabled": "NORMALIZE"}


def _settings_defaults() -> dict[str, object]:
    """Field defaults on the Settings class, without importing pydantic."""
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    try:
                        out[node.target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass  # non-literal default (Field(...), env lookup) — not ours
    return out


def _kwarg_defaults(module_py: Path, func_name: str) -> dict[str, object]:
    """
    Literal keyword-only defaults of a top-level function, without importing it.

    Defaults that name a module constant (`anchor_pad: float = ANCHOR_PAD`) are
    skipped: a reference cannot drift from the value it points at, so there is
    no second copy for this file to police.
    """
    tree = ast.parse(module_py.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            args = node.args
            out: dict[str, object] = {}
            for a, d in zip(args.kwonlyargs, args.kw_defaults):
                if d is None:
                    continue
                try:
                    out[a.arg] = ast.literal_eval(d)
                except ValueError:
                    pass  # a named constant, not a copied number
            return out
    raise AssertionError(f"{func_name} not found in {module_py.name}")


def _tune_constant(name: str) -> dict[str, object]:
    """The dict literal assigned to COND / BRIDGE in tune_contacts.py."""
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        # Either form: `COND = dict(...)` or the annotated `COND: dict[str, Any] = dict(...)`
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            # dict(...) call, not a {} literal
            return {kw.arg: ast.literal_eval(kw.value) for kw in value.keywords}
    raise AssertionError(f"{name} not found in {TUNE_PY.name}")


def _tune_scalar(name: str) -> object:
    """The scalar literal assigned to a module-level constant in tune_contacts.py."""
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in {TUNE_PY.name}")


class TestTuneContactsMatchesProduction:
    def test_condense_window_settings_match(self):
        settings = _settings_defaults()
        cond = _tune_constant("COND")
        assert set(cond) == set(COND_TO_SETTING), "COND gained or lost a key"
        for key, setting in COND_TO_SETTING.items():
            assert cond[key] == settings[setting], (
                f"tune_contacts COND[{key!r}]={cond[key]} but "
                f"app.config {setting}={settings[setting]} - the tuner is scoring "
                "sweeps against settings production no longer uses"
            )

    def test_motion_bridge_settings_match(self):
        settings = _settings_defaults()
        bridge = _tune_constant("BRIDGE")
        assert set(bridge) == set(BRIDGE_TO_SETTING), "BRIDGE gained or lost a key"
        for key, setting in BRIDGE_TO_SETTING.items():
            assert bridge[key] == settings[setting], (
                f"tune_contacts BRIDGE[{key!r}]={bridge[key]} but "
                f"app.config {setting}={settings[setting]}"
            )

    def test_non_condense_mirrors_match(self):
        """
        `ball_contact_scale_enabled` is copied into tune_contacts the same way
        COND and BRIDGE are, but it carries no `condense_` prefix, so the
        coverage test below cannot see it and would not notice it drifting.

        Drift here is worse than on a condense knob, not better: the switch
        moves the contact gates *and* the bridge, so a tuner mirroring the wrong
        value re-scores every row of every sweep against a detector production
        is not running.
        """
        settings = _settings_defaults()
        for setting, constant in NON_CONDENSE_TO_MIRROR.items():
            assert setting in settings, f"app.config lost {setting!r}"
            assert _tune_scalar(constant) == settings[setting], (
                f"tune_contacts {constant}={_tune_scalar(constant)} but "
                f"app.config {setting}={settings[setting]}"
            )

    @pytest.mark.parametrize("module_py", MIRROR_CONSUMERS, ids=lambda p: p.name)
    @pytest.mark.parametrize("callee", ["find_contacts", "bridge_windows_by_motion"])
    def test_the_switch_reaches_both_halves_of_the_condense_path(self, module_py, callee):
        """
        The mirrored value is only worth pinning if it is actually passed to
        every consumer. CF-174's switch is one setting precisely so the contact
        gates and the motion bridge cannot land on opposite sides of it, and the
        eval mirrors are where that wiring is easiest to drop — nothing else in
        this suite would notice, because both tools need a ball track from R2 to
        run at all.

        Parametrized over the *modules*, not just the callees, because the first
        version of this test scanned only `tune_contacts` — the file the constant
        is declared in — and therefore could not see `deadtime_variants.v0_shipped`
        passing the mirror to its contacts and not to its bridge. A test whose
        docstring claims both halves are covered has to check both files, or it
        is the same class of unverified claim it exists to catch.

        Asserted over the parsed call rather than a source substring: a
        substring check passes or fails on formatting, and two earlier versions
        of this idea in the CF-174 branch were replaced for exactly that. This
        reads the keyword off the AST, so rewrapping the call is invisible and
        dropping the argument is not.
        """
        tree = ast.parse(module_py.read_text(encoding="utf-8"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name | ast.Attribute)
            and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) == callee
        ]
        assert calls, f"{module_py.name} no longer calls {callee}"
        for call in calls:
            kwargs = {k.arg for k in call.keywords if k.arg}
            assert "normalize" in kwargs, (
                f"{module_py.name} calls {callee} at line {call.lineno} without "
                "normalize=, so that half of the run ignores the CF-174 switch"
            )

    def test_every_condense_setting_is_covered(self):
        """A new condense_* knob should be mapped here, not silently skipped."""
        settings = _settings_defaults()
        condense_settings = {k for k in settings if k.startswith("condense_")}
        mapped = (
            set(COND_TO_SETTING.values())
            | set(BRIDGE_TO_SETTING.values())
            | set(GUARD_TO_KWARG)
            | SWITCH_SETTINGS
        )
        assert condense_settings == mapped, (
            f"unmapped condense settings: {sorted(condense_settings - mapped)} - "
            "add them to tune_contacts and to the maps in this test"
        )


class TestGuardedDefaultsMatchProduction:
    """
    The guarded builder's kwarg defaults are a second copy of the
    condense_guard_* settings: the eval harness scores v5 through those defaults
    (no app installed), production passes the settings. Drift and the comparison
    silently starts describing a configuration nothing runs.
    """

    def test_guard_settings_match_builder_defaults(self):
        settings = _settings_defaults()
        defaults = _kwarg_defaults(DEAD_TIME_PY, "active_windows_guarded")
        for setting, kwarg in GUARD_TO_KWARG.items():
            assert kwarg in defaults, f"active_windows_guarded lost the {kwarg!r} kwarg"
            assert defaults[kwarg] == settings[setting], (
                f"active_windows_guarded {kwarg}={defaults[kwarg]} but app.config "
                f"{setting}={settings[setting]} - deadtime_variants.v5 is scoring "
                "a configuration production does not run"
            )

    def test_condense_mode_default_is_a_known_mode(self):
        mode = _settings_defaults()["condense_mode"]
        assert mode in {"rules", "guarded"}, f"unknown condense_mode default {mode!r}"
