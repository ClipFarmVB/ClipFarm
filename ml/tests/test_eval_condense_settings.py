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


def _tune_constant(name: str) -> dict[str, object]:
    """The dict literal assigned to COND / BRIDGE in tune_contacts.py."""
    tree = ast.parse(TUNE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            # dict(...) call, not a {} literal
            return {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
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
        mapped = set(COND_TO_SETTING.values()) | set(BRIDGE_TO_SETTING.values())
        assert condense_settings == mapped, (
            f"unmapped condense settings: {sorted(condense_settings - mapped)} - "
            "add them to tune_contacts and to the maps in this test"
        )
