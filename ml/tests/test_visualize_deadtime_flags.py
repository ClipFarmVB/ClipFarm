"""
CF-391's two flags on `visualize_deadtime`, which the README's condense ladder is
measured with.

`--dump-windows` is only useful if the harness can read what it writes back: the
`CF-391-rebaseline` rows are recorded from those files through
`--windows-json`. `--contact-scale` is only truthful if it reaches the globals
`load_game` and `v0_shipped` actually read — a flag that parsed and changed
nothing would label switch-ON figures as switch-OFF ones.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="deadtime_variants does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.eval.deadtime_variants as dv  # noqa: E402
import ml.eval.visualize_deadtime as vd  # noqa: E402
from ml.eval.harness import load_windows_json, refuse_contact_scale_mismatch  # noqa: E402


def test_a_dump_reads_back_through_the_harness(tmp_path):
    windows = [(1.23456, 10.0), (20.0, 33.33349)]
    path = vd.dump_windows(tmp_path, "test9", "v5", windows, "soft")
    assert path == tmp_path / "test9_v5.json"
    assert load_windows_json(path) == [(1.235, 10.0), (20.0, 33.333)]
    assert json.loads(path.read_text(encoding="utf-8"))["contact_scale"] == "soft"


def _snapshot(enabled, soft):
    return {"app.config": {"ball_contact_scale_enabled": enabled,
                           "ball_contact_soft_scale_enabled": soft}}


@pytest.mark.parametrize("position, enabled, soft", [
    ("off", False, False),
    ("off", False, True),  # the soft flag is read only while the switch is on
    ("on", True, False),
    ("soft", True, True),
])
def test_the_harness_accepts_a_dump_its_snapshot_agrees_with(tmp_path, position, enabled, soft):
    path = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], position)
    refuse_contact_scale_mismatch(path, _snapshot(enabled, soft))


@pytest.mark.parametrize("position, enabled, soft", [
    ("on", False, False),   # the review's case: switch-on windows, BALL_CONTACT_SCALE_ENABLED=false
    ("soft", False, False),
    ("off", True, False),
    ("on", True, True),
    ("soft", True, False),
])
def test_the_harness_refuses_a_dump_its_snapshot_disagrees_with(tmp_path, position, enabled, soft):
    path = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], position)
    with pytest.raises(SystemExit, match="contact scale"):
        refuse_contact_scale_mismatch(path, _snapshot(enabled, soft))


def test_a_dump_without_a_position_or_settings_is_not_refused(tmp_path):
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"keep": [{"start": 0, "end": 1}]}), encoding="utf-8")
    refuse_contact_scale_mismatch(bare, _snapshot(True, False))
    labelled = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], "on")
    refuse_contact_scale_mismatch(labelled, {})


class _Loaded(Exception):
    pass


@pytest.mark.parametrize("position, normalize, soft", [
    ("off", False, False),
    ("on", True, False),
    ("soft", True, True),
])
def test_contact_scale_reaches_what_load_game_reads(monkeypatch, position, normalize, soft):
    # Registered first so monkeypatch puts the real values back after main()
    # has overwritten them.
    monkeypatch.setattr(dv, "NORMALIZE", dv.NORMALIZE)
    monkeypatch.setattr(dv, "SOFT_SCALE", dv.SOFT_SCALE)
    seen = []

    def fake_load_game(test_id):
        seen.append((dv.NORMALIZE, dv.SOFT_SCALE))
        raise _Loaded

    monkeypatch.setattr(vd, "load_game", fake_load_game)
    with pytest.raises(_Loaded):
        vd.main(["--contact-scale", position])
    assert seen == [(normalize, soft)]


@pytest.mark.parametrize("normalize, soft, position", [
    (False, False, "off"),
    (True, False, "on"),
    (True, True, "soft"),
])
def test_the_default_is_the_mirror_and_moves_nothing(monkeypatch, normalize, soft, position):
    # Each mirror state in turn, so a default hard-coded to any one position
    # fails on the other two rather than passing while the mirror matches it.
    monkeypatch.setattr(dv, "NORMALIZE", normalize)
    monkeypatch.setattr(dv, "SOFT_SCALE", soft)
    assert vd.parse_args([]).contact_scale == position
    vd.apply_contact_scale(vd.parse_args([]).contact_scale)
    assert (dv.NORMALIZE, dv.SOFT_SCALE) == (normalize, soft)


def test_a_second_run_in_process_starts_from_the_mirror(monkeypatch):
    # No monkeypatch on the globals: main() itself has to put them back, or the
    # second run's default is the first run's --contact-scale.
    original = (dv.NORMALIZE, dv.SOFT_SCALE)
    original_disable = logging.root.manager.disable
    # A level of our own, neither the default nor the INFO main() sets: in suite
    # order earlier tests' main() calls may already have left INFO behind, and a
    # baseline read off them would make a dropped restore compare INFO with INFO.
    logging.disable(logging.WARNING)
    # The first run has to move off the mirror, or a leak would be invisible.
    first, first_globals = ("soft", (True, True)) if original != (True, True) else ("off", (False, False))
    seen = []

    def fake_load_game(test_id):
        seen.append((dv.NORMALIZE, dv.SOFT_SCALE))
        raise _Loaded

    monkeypatch.setattr(vd, "load_game", fake_load_game)
    try:
        with pytest.raises(_Loaded):
            vd.main(["--contact-scale", first])
        with pytest.raises(_Loaded):
            vd.main([])
        assert seen == [first_globals, original]
        assert (dv.NORMALIZE, dv.SOFT_SCALE) == original
        assert logging.root.manager.disable == logging.WARNING
    finally:
        dv.NORMALIZE, dv.SOFT_SCALE = original
        logging.disable(original_disable)


def test_an_unknown_position_is_refused():
    with pytest.raises(ValueError):
        vd.apply_contact_scale("half")


# ── CF-229: --static-site-filter ─────────────────────────────────────────────

@pytest.mark.parametrize("flag, expected", [("on", True), ("off", False)])
def test_static_site_filter_reaches_what_load_game_reads_and_is_put_back(monkeypatch, flag, expected):
    original = dv.STATIC_SITE_FILTER
    seen = []

    def fake_load_game(test_id):
        seen.append(dv.STATIC_SITE_FILTER)
        raise _Loaded

    monkeypatch.setattr(vd, "load_game", fake_load_game)
    with pytest.raises(_Loaded):
        vd.main(["--static-site-filter", flag])
    assert seen == [expected]
    assert dv.STATIC_SITE_FILTER is original


@pytest.mark.parametrize("mirror, position", [(False, "off"), (True, "on")])
def test_the_static_site_default_is_the_mirror(monkeypatch, mirror, position):
    monkeypatch.setattr(dv, "STATIC_SITE_FILTER", mirror)
    assert vd.parse_args([]).static_site_filter == position


def _site_snapshot(enabled):
    snap = _snapshot(True, False)
    snap["app.config"]["ball_static_site_filter_enabled"] = enabled
    return snap


@pytest.mark.parametrize("position, enabled", [("on", True), ("off", False)])
def test_the_harness_accepts_a_static_site_position_its_snapshot_agrees_with(tmp_path, position, enabled):
    path = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], "on", position)
    assert json.loads(path.read_text(encoding="utf-8"))["static_site_filter"] == position
    refuse_contact_scale_mismatch(path, _site_snapshot(enabled))


@pytest.mark.parametrize("position, enabled", [("on", False), ("off", True)])
def test_the_harness_refuses_a_static_site_position_its_snapshot_disagrees_with(tmp_path, position, enabled):
    path = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], "on", position)
    with pytest.raises(SystemExit, match="static-site filter"):
        refuse_contact_scale_mismatch(path, _site_snapshot(enabled))


def test_a_snapshot_from_before_the_filter_does_not_refuse_a_dump(tmp_path):
    # A settings snapshot without the key (an app older than CF-229) passes,
    # the way a dump without "contact_scale" does.
    path = vd.dump_windows(tmp_path, "test9", "v5", [(0.0, 1.0)], "on", "on")
    refuse_contact_scale_mismatch(path, _snapshot(True, False))
