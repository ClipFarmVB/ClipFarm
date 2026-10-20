"""CF-103 (#116): `tune_contacts --loo`, the multi-fixture leave-one-game-out mode.

Two halves. The CANDIDATES table gets three of CF-309's guards — no default
pinned, only declared tunables, no two rows the same run — because a candidate
pinning a knob at its shipping default re-scores the shipping row under another
name, and the shipping row is already printed first. And the LOO machinery is
run on synthetic games, no fixture or cache, with each case built to be able
to fail: the pick must change when the held-out game changes, the switch must
reach `score_setting`, and the shipping row has to be beaten rather than tied.
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import tune_contacts as TC  # noqa: E402
from ml.pipeline import ball as B  # noqa: E402
from ml.pipeline.ball import BallPosition, TrackedBall  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(caplog):
    caplog.set_level(logging.CRITICAL, logger="ml.pipeline.ball")


# ── the CANDIDATES table ────────────────────────────────────────────────────

def test_the_candidates_were_actually_found():
    """An empty table makes every parametrized case below vacuous."""
    assert TC.CANDIDATES


@pytest.mark.parametrize("name", sorted(TC.CANDIDATES))
def test_no_candidate_pins_a_knob_to_its_shipping_default(name):
    """CF-309, for this table. The stage-2 original pinned
    `CONTACT_HIT_SPEED_PXPS=240`, the default, in two rows — one of them was the
    shipping row twice, the other only `resid 180` under a longer label."""
    noops = {k: v for k, v in TC.CANDIDATES[name].items() if getattr(B, k) == v}
    assert not noops, f"{name!r} pins {noops} at the shipping default"


@pytest.mark.parametrize("name", sorted(TC.CANDIDATES))
def test_every_candidate_override_is_a_declared_tunable(name):
    stray = sorted(set(TC.CANDIDATES[name]) - set(TC.TUNABLES))
    assert not stray, f"{name!r} overrides {stray}, which are not TUNABLES"


def test_no_two_candidates_are_the_same_run():
    seen: list[tuple[str, dict]] = []
    for name, cfg in TC.CANDIDATES.items():
        assert cfg, f"{name!r} overrides nothing — it is the shipping row"
        clash = [prev for prev, o in seen if o == cfg]
        assert not clash, f"{name!r} scores exactly what {clash[0]!r} scores"
        seen.append((name, cfg))


def test_the_shipping_row_is_computed_and_first():
    """Not listed in CANDIDATES, so it follows ball.py when a default moves."""
    rows = TC.loo_configs()
    assert list(rows)[0] == TC.SHIPPING_LABEL and rows[TC.SHIPPING_LABEL] == {}
    assert TC.SHIPPING_LABEL not in TC.CANDIDATES


def test_knob_mode_scores_that_knobs_sweep_values():
    rows = TC.loo_configs("CONTACT_HIT_SPEED_PXPS")
    assert [cfg for name, cfg in rows.items() if name != TC.SHIPPING_LABEL] == [
        {"CONTACT_HIT_SPEED_PXPS": v} for v in TC.SWEEPS["CONTACT_HIT_SPEED_PXPS"]]


def test_the_switches_are_both_positions_and_soft():
    assert TC.LOO_SWITCHES == {"off": (False, False), "on": (True, False),
                               "soft": (True, True)}


def test_the_tuned_games_and_guards_do_not_overlap():
    assert len(TC.LOO_FIXTURES) >= 2
    assert not set(TC.LOO_FIXTURES) & set(TC.LOO_GUARDS)


# ── the pick ────────────────────────────────────────────────────────────────

def _cell(dead_sec: float, live: float) -> dict:
    m = dict(dead=0.5, live=live, recall=0.9, dead_sec=dead_sec)
    return dict(hit=1, spans=1, rules=m, guarded=m)


def test_the_pick_is_made_on_the_other_games_only():
    """"a" wins g1 hugely and loses g2/g3; "b" wins g2/g3. Held out g1, the pick
    sees only g2/g3 and must be "b" even though "a" wins the total; held out
    g2, the pick sees g1's 1000 and takes "a". A pick that read the held-out
    game would never print a different config per row."""
    table = {
        "shipping": {"g1": _cell(0, 0), "g2": _cell(0, 0), "g3": _cell(0, 0)},
        "a": {"g1": _cell(1000, 0), "g2": _cell(0, 10), "g3": _cell(0, 10)},
        "b": {"g1": _cell(0, 0), "g2": _cell(50, 0), "g3": _cell(50, 0)},
    }
    picks = dict(TC.loo_picks(table, ["g1", "g2", "g3"], "rules"))
    assert picks == {"g1": "b", "g2": "a", "g3": "a"}


def test_a_tie_goes_to_shipping():
    table = {"shipping": {"g1": _cell(10, 0), "g2": _cell(10, 0)},
             "a": {"g1": _cell(10, 0), "g2": _cell(10, 0)}}
    assert dict(TC.loo_picks(table, ["g1", "g2"], "rules")) == {"g1": "shipping",
                                                               "g2": "shipping"}


def test_utility_charges_live_cut_at_the_cost_ratio():
    assert TC.loo_utility({"dead_sec": 100.0, "live": 10.0}) == 100.0 - TC.LIVE_CUT_COST * 10


def test_the_pick_is_made_per_builder():
    table = {
        "shipping": {g: dict(hit=1, spans=1, rules=_cell(0, 0)["rules"],
                             guarded=_cell(0, 0)["guarded"]) for g in ("g1", "g2")},
        "a": {g: dict(hit=1, spans=1, rules=_cell(100, 0)["rules"],
                      guarded=_cell(0, 100)["guarded"]) for g in ("g1", "g2")},
    }
    assert {p for _, p in TC.loo_picks(table, ["g1", "g2"], "rules")} == {"a"}
    assert {p for _, p in TC.loo_picks(table, ["g1", "g2"], "guarded")} == {"shipping"}


# ── end to end on synthetic games ───────────────────────────────────────────

def _reversal_game(test_id: str, frame_height: int, speed_fhps: float):
    """A ball out and back at `speed_fhps` frame-heights/s, then a long gap:
    one candidate contact, labelled in play, with dead time either side."""
    positions = []
    x = 0.2 * frame_height
    for i in range(16):
        positions.append(BallPosition(frame=i, time=30.0 + i * 0.33, x=x,
                                      y=0.5 * frame_height, confidence=0.8))
        x += (1.0 if i < 8 else -1.0) * speed_fhps * frame_height * 0.33
    track = TrackedBall(positions=positions)
    dicts = [{"time": p.time, "x": p.x, "y": p.y, "confidence": p.confidence}
             for p in positions]
    fx = SimpleNamespace(test_id=test_id, duration=120.0, keep=[(29.0, 36.0)], raw={})
    return track, dicts, frame_height, fx


def test_the_switch_reaches_scoring(monkeypatch, capsys):
    """At 1080p a 0.4 fh/s reversal is 432 px/s: over the unscaled 240 floor
    (switch off) and under the 3x-scaled 720 (switch on); soft at e=0.5 gates
    at ~416, so it passes again. Three positions, two different contact
    counts — a switch that never reached `score_setting` would print one."""
    games = {t: _reversal_game(t, 1080, 0.4) for t in ("g1", "g2")}
    monkeypatch.setattr(TC, "load_track", lambda tid: games[tid])
    monkeypatch.setattr(TC, "CANDIDATES", {"hit 220": {"CONTACT_HIT_SPEED_PXPS": 220.0}})
    hits = {}
    for switch in ("off", "on", "soft"):
        normalize, soft = TC.LOO_SWITCHES[switch]
        table = TC.loo_table(games, TC.loo_configs(), normalize=normalize, soft_scale=soft)
        hits[switch] = table[TC.SHIPPING_LABEL]["g1"]["hit"]
    assert hits == {"off": 1, "on": 0, "soft": 1}


def test_the_cli_prints_both_builders_and_every_held_out_game(monkeypatch, capsys):
    games = {t: _reversal_game(t, 360, 1.0) for t in ("g1", "g2", "g3")}
    monkeypatch.setattr(TC, "load_track", lambda tid: games[tid])
    monkeypatch.setattr(TC, "LOO_GUARDS", ("g3",))
    TC.main(["--loo", "--switch", "off", "g1", "g2"])
    out = capsys.readouterr().out
    assert "switch off" in out
    for builder in ("rules", "guarded"):
        assert f"-- {builder}: leave-one-game-out" in out
    assert "held out g1" in out and "held out g2" in out
    assert "held out g3" not in out, "a guard was optimized against"
    for name in TC.loo_configs():
        assert name in out


def test_a_row_that_ran_unscaled_is_not_labelled_with_the_header_scale(monkeypatch, capsys):
    """#609 R1-M1. The header's scale is computed at shipping constants, outside
    any override. `CONTACT_RESIDUAL_MIN_PXPS=360` moves `_scale_for`'s clamp to
    960p, so at 1080p that row runs unscaled under a header reading 3.00. The
    row must say the scale it actually ran at; rows that kept the header's
    scale must not be flagged, or the note means nothing."""
    games = {t: _reversal_game(t, 1080, 0.4) for t in ("g1", "g2")}
    monkeypatch.setattr(TC, "load_track", lambda tid: games[tid])
    monkeypatch.setattr(TC, "LOO_GUARDS", ())
    TC.main(["--loo", "--switch", "on", "--knob", "CONTACT_RESIDUAL_MIN_PXPS", "g1", "g2"])
    lines = capsys.readouterr().out.splitlines()
    assert any("g1" in ln and "scale 3.00" in ln for ln in lines), "header moved"
    reverted = "CONTACT_RESIDUAL_MIN_PXPS=360"
    rows = [i for i, ln in enumerate(lines)
            if ln.startswith((TC.SHIPPING_LABEL, "CONTACT_RESIDUAL_MIN_PXPS="))]
    assert len(rows) == 2 * len(TC.loo_configs("CONTACT_RESIDUAL_MIN_PXPS"))
    for i in rows:
        note = lines[i + 1] if i + 1 < len(lines) else ""
        if lines[i].startswith(reverted + " "):
            assert note.strip() == "^ scale applied: g1 1.00, g2 1.00", lines[i]
        else:
            assert not note.lstrip().startswith("^"), lines[i]


def test_a_repeated_tuned_id_is_not_two_games(monkeypatch):
    """#609 R1-N1: `--loo g1 g1` would hold g1 out against an empty training
    set, where every config ties at U=0 and shipping prints as the pick."""
    monkeypatch.setattr(TC, "load_track", lambda tid: _reversal_game(tid, 360, 1.0))
    with pytest.raises(SystemExit):
        TC.main(["--loo", "g1", "g1"])


def test_soft_without_normalize_defaults_to_off(monkeypatch):
    """#609 R1-N2: soft is ignored while normalize is off."""
    monkeypatch.setattr(TC, "NORMALIZE", False)
    monkeypatch.setattr(TC, "SOFT_SCALE", True)
    assert TC.default_loo_switch() == "off"


def test_scoring_restores_the_ball_constants(monkeypatch):
    games = {"g1": _reversal_game("g1", 360, 1.0)}
    before = {k: getattr(B, k) for k in TC.TUNABLES}
    TC.loo_table(games, TC.loo_configs(), normalize=True, soft_scale=False)
    assert {k: getattr(B, k) for k in TC.TUNABLES} == before


@pytest.mark.parametrize("argv", [["--loo", "--switch", "sideways"], ["--loo", "--switch"],
                                  ["--loo", "--bogus"], ["--loo", "only_one"]])
def test_bad_loo_arguments_fail_loudly(argv, monkeypatch):
    monkeypatch.setattr(TC, "load_track", lambda tid: _reversal_game(tid, 360, 1.0))
    with pytest.raises(SystemExit):
        TC.main(argv)


def test_the_default_switch_is_the_shipping_setting():
    assert TC.LOO_SWITCHES[TC.default_loo_switch()] == (TC.NORMALIZE, TC.SOFT_SCALE)
