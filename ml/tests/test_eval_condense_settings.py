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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
CONFIG_PY = REPO / "api" / "app" / "config.py"
TUNE_PY = REPO / "ml" / "eval" / "tune_contacts.py"
DEAD_TIME_PY = REPO / "ml" / "pipeline" / "dead_time.py"
VARIANTS_PY = REPO / "ml" / "eval" / "deadtime_variants.py"

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
# app.config setting -> the deadtime_variants constant the eval ladder scores it
# through (CF-198). Same drift risk as GUARD_TO_KWARG, one layer out: the v7/v8
# rows are only a statement about production if the ladder runs production's
# threshold.
#
# Only the anchor is pinned. The gate is deliberately *unequal* — the ladder
# scores v6 at 0.80 to show what the gate does, production ships it disabled
# because that number lost on the held-out fixtures — so pinning it would
# enforce an agreement that is not supposed to hold.
POSE_TO_VARIANT = {
    "condense_pose_anchor_activity": "POSE_ANCHOR_ACTIVITY",
}
# app.config setting -> the dead_time.py module constant it copies. Same drift
# risk again: the eval ladder picks these up as kwarg defaults while production
# passes the setting, so the two must agree.
POSE_TO_DEAD_TIME_CONST = {
    "condense_pose_anchor_min_seconds": "POSE_ANCHOR_MIN_SECONDS",
}
# Mode switches, not tunables: not copied into tune_contacts, which sweeps the
# rule-based path only. Listed here so the coverage test stays a conscious
# checkpoint for every new condense_* knob.
SWITCH_SETTINGS = {
    "condense_mode",
    # CF-198. condense_use_pose gates the whole pose pass; sample_fps is a cost
    # lever with no eval-side twin (the caches are built at 3.0 by
    # build_pose_cache's own default); gate_activity ships disabled, see above.
    "condense_use_pose",
    "condense_pose_sample_fps",
    "condense_pose_gate_activity",
}


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


def _module_constants(module_py: Path) -> dict[str, object]:
    """Literal module-level assignments, without importing (see the docstring)."""
    tree = ast.parse(module_py.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
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

    def test_every_condense_setting_is_covered(self):
        """A new condense_* knob should be mapped here, not silently skipped."""
        settings = _settings_defaults()
        condense_settings = {k for k in settings if k.startswith("condense_")}
        mapped = (
            set(COND_TO_SETTING.values())
            | set(BRIDGE_TO_SETTING.values())
            | set(GUARD_TO_KWARG)
            | set(POSE_TO_VARIANT)
            | set(POSE_TO_DEAD_TIME_CONST)
            | SWITCH_SETTINGS
        )
        assert condense_settings == mapped, (
            f"unmapped condense settings: {sorted(condense_settings - mapped)} - "
            "add them to tune_contacts and to the maps in this test"
        )


class TestPoseLadderMatchesProduction:
    """
    CF-198: the pose rungs must score the threshold production would run, for
    the same reason v5 must — otherwise the README's table describes a
    configuration nothing ships.
    """

    def test_anchor_threshold_matches(self):
        settings = _settings_defaults()
        variants = _module_constants(VARIANTS_PY)
        for setting, const in POSE_TO_VARIANT.items():
            assert const in variants, f"deadtime_variants lost {const}"
            assert variants[const] == settings[setting], (
                f"deadtime_variants {const}={variants[const]} but app.config "
                f"{setting}={settings[setting]} - the ladder is scoring a "
                "threshold production does not run"
            )

    def test_anchor_shape_constants_match(self):
        settings = _settings_defaults()
        consts = _module_constants(DEAD_TIME_PY)
        for setting, const in POSE_TO_DEAD_TIME_CONST.items():
            assert const in consts, f"dead_time lost {const}"
            assert consts[const] == settings[setting], (
                f"dead_time {const}={consts[const]} but app.config "
                f"{setting}={settings[setting]}"
            )

    def test_the_pose_anchor_is_shorter_than_the_ball_anchor(self):
        """
        The whole point of the separate constant: player activity is burstier
        than ball flight, so reusing ANCHOR_MIN_SECONDS discards the sub-second
        bursts this signal exists to catch. If someone collapses the two back
        into one constant, this fails.
        """
        consts = _module_constants(DEAD_TIME_PY)
        assert consts["POSE_ANCHOR_MIN_SECONDS"] < consts["ANCHOR_MIN_SECONDS"]

    def test_the_gate_ships_disabled(self):
        """
        Pinned as an intent, not an accident: the gate beat v5 on the two
        fixtures it was tuned on and lost on both held-out ones, so it ships off.
        Re-arming it should be a deliberate edit that fails this test first.
        """
        assert _settings_defaults()["condense_pose_gate_activity"] is None


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
