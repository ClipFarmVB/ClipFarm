"""CF-376 (#476): the helpers behind `tune_contacts <id> --exponents`.

`score_setting`, `gate_scale` and `EXPONENT_AXIS` produced every cell of the
PR's exponent sweep and its 240-vs-220 tables, and nothing tested them. Round 5
(R5-M1) dropped the exponent override in `score_setting` and the suite stayed
green while every soft row printed the same numbers under four different scale
headers — the header comes from `gate_scale`, which takes `exponent` directly,
so the table looked plausible and was wrong.

Synthetic tracks, no fixture or cache: a fake fixture (`fx`) carries only what
`score_setting` reads from one. Each case is built to be able to fail: the
exponent cases assert the counts actually move, and the gate probes assert a
contact on one side of the floor and none on the other.
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import deadtime_variants as dv  # noqa: E402
from ml.eval import tune_contacts as TC  # noqa: E402
from ml.pipeline import ball as B  # noqa: E402
from ml.pipeline.ball import BallPosition, TrackedBall  # noqa: E402

HEIGHT = 1080

# Leg speeds in frame-heights/s at 1080p, where the full scale is 3.0. The hit
# floor is 0.667 fh/s at e=1, 0.385 at e=0.5 and 0.292 at e=0.25. A junction
# gates on the faster of its two legs, so the slow legs come in pairs: the
# 0.50-0.50 junction is admitted from e=0.5 down and the 0.35-0.35 ones only at
# e=0.25 — three exponents, three different contact sets.
LEG_SPEEDS_FHPS = (0.80, 0.50, 0.50, 0.80, 0.35, 0.35, 0.90, 0.35, 0.35, 0.80)
LEG_DIRECTIONS = ((1.0, 0.0), (-0.6, 0.8), (0.6, -0.8), (-1.0, 0.0),
                  (0.8, 0.6), (-0.8, -0.6), (1.0, 0.0), (-0.6, 0.8),
                  (0.6, -0.8), (-1.0, 0.0))


def _track(frame_height: int = HEIGHT, step: float = 0.33, per_leg: int = 5) -> TrackedBall:
    positions = []
    x, y, t, frame = 0.5 * frame_height, 0.5 * frame_height, 0.0, 0
    for speed, (dx, dy) in zip(LEG_SPEEDS_FHPS, LEG_DIRECTIONS):
        v = speed * frame_height
        for _ in range(per_leg):
            positions.append(BallPosition(frame=frame, time=round(t, 4), x=x, y=y,
                                          confidence=0.8))
            x += dx * v * step
            y += dy * v * step
            t += step
            frame += 10
    return TrackedBall(positions=positions)


def _loaded(frame_height: int = HEIGHT):
    """`load_track`'s return shape, for a synthetic fixture."""
    track = _track(frame_height)
    positions = [{"time": p.time, "x": p.x, "y": p.y, "confidence": p.confidence}
                 for p in track.positions]
    duration = track.positions[-1].time + 5.0
    fx = SimpleNamespace(test_id="synthetic", duration=duration,
                         keep=[(1.0, duration - 4.0)], raw={})
    return track, positions, frame_height, fx


def _reversal(frame_height: int, speed_pxps: float) -> TrackedBall:
    """One leg out and back at `speed_pxps`: exactly one candidate contact,
    gated at that speed (the probe `test_ball_soft_scale.py` uses)."""
    positions, x = [], 0.2 * frame_height
    for i in range(16):
        positions.append(BallPosition(frame=i, time=i * 0.33, x=x, y=0.5 * frame_height,
                                      confidence=0.8))
        x += (1.0 if i < 8 else -1.0) * speed_pxps * 0.33
    return TrackedBall(positions=positions)


@pytest.fixture(autouse=True)
def _quiet(caplog):
    # 2160p is past the clamp and logs at error on every call; not under test.
    caplog.set_level(logging.CRITICAL, logger="ml.pipeline.ball")


class TestTheExponentReachesScoring:
    """R5-M1's mutation: with the override at `score_setting` dropped, every
    soft row scored the module default while its header read its own scale."""

    def test_different_exponents_give_different_contact_counts(self):
        loaded = _loaded()
        counts = {
            e: TC.score_setting(loaded, normalize=True, soft_scale=True, exponent=e)["contacts"]
            for e in (1.0, 0.5, 0.25)
        }
        assert counts[1.0] < counts[0.5] < counts[0.25], (
            f"contact counts by exponent {counts}: the exponent is not reaching "
            f"find_contacts, so every soft row scores the same setting"
        )

    @pytest.mark.parametrize("exponent", [1.0, 0.75, 0.5, 0.25])
    def test_each_row_scores_find_contacts_at_its_exponent(self, monkeypatch, exponent):
        """Each row's contacts are find_contacts' at that exponent, and its
        `scale` is the scale that run gated on — not merely `gate_scale`'s
        answer, which would agree with itself under any mutation."""
        loaded = _loaded()
        track = loaded[0]
        row = TC.score_setting(loaded, normalize=True, soft_scale=True, exponent=exponent)
        with monkeypatch.context() as m:
            m.setattr(B, "CONTACT_SOFT_SCALE_EXPONENT", exponent)
            direct = B.find_contacts(track, frame_height=HEIGHT, soft_scale=True)
        assert row["contacts"] == len(direct)
        assert row["scale"] == pytest.approx(
            (HEIGHT / B.REFERENCE_FRAME_HEIGHT) ** exponent)

    def test_e1_is_the_switch_on_row_again(self):
        """The axis' control: x ** 1.0 == x, so soft at e=1 scores switch-on."""
        loaded = _loaded()
        on = TC.score_setting(loaded, normalize=True, soft_scale=False)
        e1 = TC.score_setting(loaded, normalize=True, soft_scale=True, exponent=1.0)
        assert e1 == on

    def test_exponent_none_is_the_module_constant(self):
        loaded = _loaded()
        default = TC.score_setting(loaded, normalize=True, soft_scale=True)
        explicit = TC.score_setting(loaded, normalize=True, soft_scale=True,
                                    exponent=B.CONTACT_SOFT_SCALE_EXPONENT)
        assert default == explicit


# (frame_height, normalize, soft_scale, exponent). Full scale, soft at several
# exponents and two heights, then the three places the scale is exactly 1.0.
GATE_CASES = [
    (1080, True, False, None),
    (1080, True, False, 0.25),     # exponent ignored with the flag off
    (1080, True, True, 0.75),
    (1080, True, True, 0.5),
    (1080, True, True, 0.25),
    (720, True, True, 0.25),
    (360, True, True, 0.25),
    (2160, True, True, 0.25),      # past the clamp point
    (1080, False, True, 0.25),     # switch off
]
UNIT_SCALE_CASES = [(360, True, True, 0.25), (2160, True, True, 0.25),
                    (1080, False, True, 0.25)]


class TestGateScaleAgreesWithFindContacts:
    """`gate_scale` is the table header; find_contacts is what gates. They are
    two copies of one rule, so the header is pinned to the gate by probing the
    gate: a single redirect just above the floor `gate_scale` names must be
    found, and one just below it must not."""

    @pytest.mark.parametrize("height,normalize,soft,exponent", GATE_CASES)
    def test_the_header_is_the_scale_find_contacts_applies(
            self, monkeypatch, height, normalize, soft, exponent):
        # Asked with the module constant at its default, so a gate_scale that
        # read the constant instead of `exponent` names the wrong floor.
        scale = TC.gate_scale(height, normalize=normalize, soft_scale=soft,
                              exponent=exponent)
        floor = B.CONTACT_HIT_SPEED_PXPS * scale
        found = {}
        with monkeypatch.context() as m:
            if exponent is not None:
                m.setattr(B, "CONTACT_SOFT_SCALE_EXPONENT", exponent)
            for side, factor in (("above", 1.03), ("below", 0.97)):
                found[side] = len(B.find_contacts(
                    _reversal(height, floor * factor), frame_height=height,
                    normalize=normalize, soft_scale=soft))
        assert found == {"above": 1, "below": 0}, (
            f"gate_scale says {scale:.3f} at {height}p (normalize={normalize}, "
            f"soft={soft}, exponent={exponent}), but find_contacts does not gate "
            f"at {floor:.0f} px/s: {found}"
        )

    @pytest.mark.parametrize("height,normalize,soft,exponent", UNIT_SCALE_CASES)
    def test_unit_scale_where_find_contacts_has_none(self, height, normalize, soft, exponent):
        assert TC.gate_scale(height, normalize=normalize, soft_scale=soft,
                             exponent=exponent) == 1.0

    def test_exponent_none_reads_the_module_constant(self, monkeypatch):
        monkeypatch.setattr(B, "CONTACT_SOFT_SCALE_EXPONENT", 0.25)
        assert TC.gate_scale(1080, normalize=True, soft_scale=True) == pytest.approx(
            3.0 ** 0.25)


class TestScoreSettingRestoresConstants:
    """`overrides` and `exponent` are module-global assignments, so a leak
    re-scores every later row (and every later test) at someone else's setting.
    8840b7a moved the restore after scoring; the spies below pin both halves:
    in force throughout, restored afterwards."""

    OVERRIDES = {"MIN_RALLY_CONTACTS": 1, "CONTACT_HIT_SPEED_PXPS": 100.0}
    EXPONENT = 0.25
    NAMES = ("MIN_RALLY_CONTACTS", "CONTACT_HIT_SPEED_PXPS", "CONTACT_SOFT_SCALE_EXPONENT")

    def _expected_in_force(self):
        return {**self.OVERRIDES, "CONTACT_SOFT_SCALE_EXPONENT": self.EXPONENT}

    def _spy(self, monkeypatch, target, name, seen):
        real = getattr(target, name)

        def spy(*a, **kw):
            seen[name] = {k: getattr(B, k) for k in self.NAMES}
            return real(*a, **kw)
        monkeypatch.setattr(target, name, spy)

    def test_in_force_through_scoring_and_restored_after(self, monkeypatch):
        defaults = {k: getattr(B, k) for k in self.NAMES}
        assert defaults != self._expected_in_force(), "overrides must differ from the defaults"
        seen: dict = {}
        self._spy(monkeypatch, B, "find_contacts", seen)
        self._spy(monkeypatch, B, "contacts_to_rallies", seen)
        self._spy(monkeypatch, dv, "v5_guarded", seen)

        TC.score_setting(_loaded(), normalize=True, soft_scale=True,
                         exponent=self.EXPONENT, overrides=self.OVERRIDES)

        for name in ("find_contacts", "contacts_to_rallies", "v5_guarded"):
            assert seen.get(name) == self._expected_in_force(), (
                f"{name} ran without the overrides in force: {seen.get(name)}")
        assert {k: getattr(B, k) for k in self.NAMES} == defaults

    def test_restored_after_an_exception(self, monkeypatch):
        defaults = {k: getattr(B, k) for k in self.NAMES}
        seen: dict = {}

        def boom(*a, **kw):
            seen["in_force"] = {k: getattr(B, k) for k in self.NAMES}
            raise RuntimeError("scoring failed")
        monkeypatch.setattr(B, "contacts_to_rallies", boom)

        with pytest.raises(RuntimeError, match="scoring failed"):
            TC.score_setting(_loaded(), normalize=True, soft_scale=True,
                             exponent=self.EXPONENT, overrides=self.OVERRIDES)
        assert seen["in_force"] == self._expected_in_force()
        assert {k: getattr(B, k) for k in self.NAMES} == defaults

    def test_the_callers_overrides_dict_is_not_mutated(self):
        overrides = dict(self.OVERRIDES)
        TC.score_setting(_loaded(), normalize=True, soft_scale=True,
                         exponent=self.EXPONENT, overrides=overrides)
        assert overrides == self.OVERRIDES


class TestScoreSettingWiresTheBridge:
    """R9-M2: the rules column is contacts joined by `bridge_windows_by_motion`,
    and that bridge takes the row's `normalize` and never `soft_scale` — the
    combination the flag ships. Forcing `normalize=True` there turned the
    "switch off" row's test2 rules cell from 9.5% into 23.4% dead removed with
    every other test green, in the table that picks the production position.
    The spy sits on tune_contacts' own name, the one `score_setting` calls."""

    @pytest.mark.parametrize("row", TC.EXPONENT_AXIS, ids=[r[0] for r in TC.EXPONENT_AXIS])
    def test_the_bridge_takes_the_rows_normalize_and_no_soft_scale(self, monkeypatch, row):
        _label, normalize, soft, exponent = row
        calls: list[dict] = []
        real = TC.bridge_windows_by_motion

        def spy(windows, positions, **kw):
            calls.append(dict(kw))
            # Dropped before the real call so a forwarded soft_scale fails on
            # the assertion below, not on today's signature rejecting it — the
            # bridge growing that keyword is exactly the case to catch.
            kw.pop("soft_scale", None)
            return real(windows, positions, **kw)
        monkeypatch.setattr(TC, "bridge_windows_by_motion", spy)

        TC.score_setting(_loaded(), normalize=normalize, soft_scale=soft, exponent=exponent)

        assert len(calls) == 1, f"the rules path bridged {len(calls)} times"
        kw = calls[0]
        assert "soft_scale" not in kw, "soft_scale reached the bridge"
        assert kw.get("normalize") is normalize, (
            f"the bridge got normalize={kw.get('normalize')!r} on a "
            f"normalize={normalize} row")
        assert kw.get("frame_height") == HEIGHT


class TestTheAxisHoldsNoDuplicateRow:
    """CF-309's rule for SWEEPS — a row that re-scores the baseline under a
    knob's name is a hidden repeat — applied to EXPONENT_AXIS, which SWEEPS'
    guards do not reach. The axis has no "value equal to the default" to forbid
    (its baseline is the "switch on" row), so the equivalent is: no two rows
    resolve to the same setting, bar the documented e=1 control.

    The second case is the drift mechanism CF-309 started from: a soft row that
    left its exponent to the module constant would silently become a duplicate
    of whichever explicit row matched the next default."""

    @staticmethod
    def _resolved(row):
        _label, normalize, soft, exponent = row
        if not normalize:
            return (False,)
        if not soft or exponent == 1.0:
            return (True, "full")
        return (True, "soft", exponent)

    def test_no_two_rows_score_the_same_setting(self):
        # The e=1 control is the one sanctioned repeat; set it aside, once.
        rows = [r for r in TC.EXPONENT_AXIS if not (r[2] and r[3] == 1.0)]
        assert len(TC.EXPONENT_AXIS) - len(rows) <= 1, "more than one e=1 control"
        resolved = [self._resolved(r) for r in rows]
        dupes = sorted({str(s) for s in resolved if resolved.count(s) > 1})
        assert not dupes, f"EXPONENT_AXIS scores {dupes} more than once"

    def test_every_soft_row_names_its_exponent(self):
        floating = [r[0] for r in TC.EXPONENT_AXIS if r[2] and r[3] is None]
        assert not floating, (
            f"{floating} leave the exponent to the module constant; a moved "
            f"default turns them into a duplicate of another row")

    def test_each_soft_label_names_its_exponent(self):
        for label, _normalize, soft, exponent in TC.EXPONENT_AXIS:
            if soft:
                assert label.endswith(f"e={exponent:g}"), (label, exponent)
