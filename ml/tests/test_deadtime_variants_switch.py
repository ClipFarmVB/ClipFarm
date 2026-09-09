"""
The CF-174 kill switch, as the eval ladder's baseline sees it.

`deadtime_variants.v0_shipped` is what every v1..v5 is scored against, and it
reaches the switch twice: once through the contacts `load_game` builds, once
through the motion bridge it applies to them. Those two have to stay on the same
side of it. They did not — the bridge call defaulted `normalize=True` while the
contacts honoured the mirror, so flipping the mirror to match a rolled-back
production produced `main`'s contacts joined by a scaled bridge: the third,
never-measured combination CF-174 removed from tasks.py, reintroduced in the
baseline every comparison is anchored on.

The AST check in test_eval_condense_settings.py pins that the keyword is passed.
This pins what passing it *does*, which is the half a wiring check cannot see.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="deadtime_variants does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.eval.deadtime_variants as dv  # noqa: E402

FRAME_H = 1080
DURATION = 60.0


def _ball_path(t0: float, t1: float, speed_pxps: float, step: float = 0.2):
    """A ball crossing the gap at a fixed px/s."""
    out, t, x = [], 0.0, 0.0
    while t <= DURATION:
        out.append({"time": round(t, 3), "x": x, "y": 100.0})
        x += (speed_pxps if t0 <= t <= t1 else 0.0) * step
        t += step
    return out


def _game(**over) -> dv.Game:
    base = dict(
        test_id="synthetic", video_file="synthetic.mp4", duration=DURATION,
        frame_height=FRAME_H,
        # 300 px/s through the gap: "fast" at the reference height (150) and
        # ordinary handling once scaled to 1080p (450). The whole case turns on
        # which of those two thresholds v0_shipped ends up applying.
        positions=_ball_path(12.0, 26.0, 300.0),
        contacts=[{"time": 5.0}, {"time": 8.0}, {"time": 30.0}, {"time": 33.0}],
        human_keep=[(0.0, DURATION)], raw={},
    )
    base.update(over)
    return dv.Game(**base)


class TestV0ShippedHonoursTheSwitchOnBothHalves:
    def test_on_scales_the_bridge_so_the_gap_survives(self, monkeypatch):
        monkeypatch.setattr(dv, "NORMALIZE", True)
        windows = dv.v0_shipped(_game())
        assert len(windows) > 1, (
            "at 1080p the scaled threshold is 450 px/s, so a 300 px/s crossing "
            "is ordinary handling and must not bridge"
        )

    def test_off_restores_mains_unscaled_bridge_and_the_gap_closes(self, monkeypatch):
        monkeypatch.setattr(dv, "NORMALIZE", False)
        windows = dv.v0_shipped(_game())
        assert len(windows) == 1, (
            "with the switch off the threshold is `main`'s 150 px/s, so the same "
            "300 px/s crossing bridges — if this still splits, v0_shipped is "
            "applying a scaled bridge to unscaled contacts"
        )

    def test_the_two_halves_move_together(self, monkeypatch):
        """
        The failure this file exists for is not "the bridge is wrong", it is
        "the two halves disagree". Asserted as a difference, so the case cannot
        pass by both halves being stuck on the same wrong side.
        """
        monkeypatch.setattr(dv, "NORMALIZE", True)
        on = dv.v0_shipped(_game())
        monkeypatch.setattr(dv, "NORMALIZE", False)
        off = dv.v0_shipped(_game())
        assert on != off, (
            "the switch changed nothing in the eval baseline, so at least one "
            "half of v0_shipped is ignoring it"
        )
