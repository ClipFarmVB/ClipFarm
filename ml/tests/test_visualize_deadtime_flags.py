"""
CF-391's two flags on `visualize_deadtime`, which the README's condense ladder is
measured with.

`--dump-windows` is only useful if the harness can read what it writes back: the
`CF-391-rebaseline` rows are recorded from those files through
`--windows-json`. `--contact-scale` is only truthful if it reaches the globals
`load_game` and `v0_shipped` actually read — a flag that parsed and changed
nothing would label switch-ON figures as switch-OFF ones.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="deadtime_variants does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.eval.deadtime_variants as dv  # noqa: E402
import ml.eval.visualize_deadtime as vd  # noqa: E402
from ml.eval.harness import load_windows_json  # noqa: E402


def test_a_dump_reads_back_through_the_harness(tmp_path):
    windows = [(1.23456, 10.0), (20.0, 33.33349)]
    path = vd.dump_windows(tmp_path, "test9", "v5", windows)
    assert path == tmp_path / "test9_v5.json"
    assert load_windows_json(path) == [(1.235, 10.0), (20.0, 33.333)]


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


def test_the_default_is_the_mirror_and_moves_nothing(monkeypatch):
    monkeypatch.setattr(dv, "NORMALIZE", dv.NORMALIZE)
    monkeypatch.setattr(dv, "SOFT_SCALE", dv.SOFT_SCALE)
    before = (dv.NORMALIZE, dv.SOFT_SCALE)
    vd.apply_contact_scale(vd.parse_args([]).contact_scale)
    assert (dv.NORMALIZE, dv.SOFT_SCALE) == before


def test_an_unknown_position_is_refused():
    with pytest.raises(ValueError):
        vd.apply_contact_scale("half")
