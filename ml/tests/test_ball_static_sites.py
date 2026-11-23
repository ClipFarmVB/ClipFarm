"""
CF-229 (#233): `drop_static_sites` and the `filter_static_sites` switch around it.

The filter ships off, and off has to be `main` exactly — the same tracker
object, untouched. On, it drops dwells at a recurring site (a fixed object the
tracker parks on) and nothing else. Every positive case below is paired with a
near miss that must survive, so a filter that drops too much fails as loudly as
one that drops too little.

Tracks are synthetic, sampled at the pipeline's ~3 samples/s (0.333s apart).
"""
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="ml/pipeline/ball.py does its maths in numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ml.pipeline.ball as ball  # noqa: E402
from ml.pipeline.ball import (  # noqa: E402
    BallPosition,
    TrackedBall,
    _static_site_radius,
    drop_static_sites,
    filter_static_sites,
)

DT = 1 / 3
SITE = (500.0, 300.0)


class _Track:
    """Builds a track in time order, one position per DT."""

    def __init__(self):
        self.positions: list[BallPosition] = []
        self.t = 0.0

    def add(self, x: float, y: float) -> BallPosition:
        p = BallPosition(frame=int(round(self.t * 30)), time=round(self.t, 4),
                         x=x, y=y, confidence=0.9)
        self.positions.append(p)
        self.t += DT
        return p

    def dwell(self, n: int, at=SITE, jitter: float = 1.0) -> list[BallPosition]:
        """n positions at one spot, wandering by `jitter` px as a lock-on does."""
        return [self.add(at[0] + jitter * (i % 2), at[1] - jitter * (i % 3 == 0))
                for i in range(n)]

    def flight(self, n: int, start=(100.0, 600.0)) -> list[BallPosition]:
        """n positions of a ball crossing the frame, ~120px per sample, nowhere near SITE."""
        x0, y0 = start
        return [self.add(x0 + 120.0 * i, y0 - 40.0 * i + 3.0 * i * i) for i in range(n)]

    def idle(self, seconds: float) -> None:
        self.t += seconds


def _site_with_flight_between(dwells: int = 3, dwell_len: int = 12, gap_s: float = 90.0):
    """`dwells` lock-ons at SITE, `gap_s` apart, real flight between them."""
    tr = _Track()
    site, moving = [], []
    for _ in range(dwells):
        site += tr.dwell(dwell_len)
        moving += tr.flight(8)
        tr.idle(gap_s)
    return tr, site, moving


class TestTheSiteIsDropped:
    def test_dwells_at_a_recurring_site_go_and_the_flight_between_stays(self):
        tr, site, moving = _site_with_flight_between()
        assert len(site) >= ball.STATIC_SITE_MIN_POSITIONS, "the probe must be a site"
        kept = drop_static_sites(tr.positions, 360)
        assert kept == moving
        # The survivors are the same objects in the same order, not copies.
        assert all(a is b for a, b in zip(kept, moving))

    def test_a_spot_seen_too_briefly_overall_is_not_a_site(self):
        # Same dwells, packed into under STATIC_SITE_MIN_SPAN_SEC: a ball held
        # for a long stoppage, not an object that is there all game.
        tr, _, _ = _site_with_flight_between(gap_s=10.0)
        span = tr.positions[-1].time - tr.positions[0].time
        assert span < ball.STATIC_SITE_MIN_SPAN_SEC
        assert drop_static_sites(tr.positions, 360) == tr.positions

    def test_a_spot_with_too_few_detections_is_not_a_site(self):
        tr, _, _ = _site_with_flight_between(dwells=2, dwell_len=10, gap_s=200.0)
        assert 20 < ball.STATIC_SITE_MIN_POSITIONS
        assert drop_static_sites(tr.positions, 360) == tr.positions


class TestAMovingBallIsKept:
    def test_a_track_of_flight_only_comes_back_unchanged(self):
        tr = _Track()
        for k in range(40):
            # Each flight shifted, as real ones are: forty identical paths
            # would make every point of them a recurring spot.
            tr.flight(10, start=(50.0 + 11.0 * k, 650.0 - 3.0 * k))
            tr.idle(5.0)
        assert len(tr.positions) == 400
        assert drop_static_sites(tr.positions, 1080) == tr.positions

    def test_flight_through_the_site_is_kept(self):
        # Real flight that happens to cross the site between dwells: lone site
        # hits, never STATIC_SITE_MIN_RUN in a row.
        tr, site, _ = _site_with_flight_between()
        crossings = []
        for _ in range(ball.STATIC_SITE_MIN_RUN - 1):
            tr.flight(3, start=(SITE[0] - 360.0, SITE[1] + 120.0))
            crossings.append(tr.add(*SITE))
            tr.flight(3, start=(SITE[0] + 120.0, SITE[1] - 40.0))
        kept = drop_static_sites(tr.positions, 360)
        assert all(any(c is k for k in kept) for c in crossings)
        assert not any(s is k for s in site for k in kept)

    def test_the_run_threshold_is_the_boundary(self):
        tr, site, _ = _site_with_flight_between()
        short = tr.dwell(ball.STATIC_SITE_MIN_RUN - 1)
        tr.flight(5)
        exact = tr.dwell(ball.STATIC_SITE_MIN_RUN)
        tr.flight(5)
        kept_ids = {id(p) for p in drop_static_sites(tr.positions, 360)}
        assert all(id(p) in kept_ids for p in short)
        assert not any(id(p) in kept_ids for p in exact)


class TestHeldServes:
    """
    The risk the filter carries. A held serve is a ball dwelling still in a
    hand for a few seconds, which looks exactly like a lock-on run. What
    separates the two is recurrence, and the behaviour on each side of it is
    pinned here so a change to it is a decision.
    """

    def test_a_serve_held_at_a_spot_nothing_else_uses_is_kept(self):
        tr, _, _ = _site_with_flight_between()
        serve = tr.dwell(10, at=(150.0, 450.0))  # ~3.3s still in the hand
        toss = tr.flight(8, start=(270.0, 410.0))
        kept_ids = {id(p) for p in drop_static_sites(tr.positions, 360)}
        assert all(id(p) in kept_ids for p in serve + toss)

    def test_serves_held_at_one_spot_all_game_are_dropped(self):
        # Known limitation, pinned rather than hidden: a server who holds the
        # ball at the same few pixels for every serve builds a site out of the
        # serves alone, and the filter cannot tell that from a fixed object.
        tr = _Track()
        serves, tosses = [], []
        for _ in range(4):
            serves += tr.dwell(10, at=(150.0, 450.0))
            tosses += tr.flight(8, start=(270.0, 410.0))
            tr.idle(60.0)
        kept_ids = {id(p) for p in drop_static_sites(tr.positions, 360)}
        assert not any(id(p) in kept_ids for p in serves)
        assert all(id(p) in kept_ids for p in tosses)

    def test_a_serve_held_at_a_fixed_objects_site_is_dropped_with_it(self):
        tr, _, _ = _site_with_flight_between()
        serve = tr.dwell(10, at=(SITE[0] + 2.0, SITE[1] + 2.0))
        kept_ids = {id(p) for p in drop_static_sites(tr.positions, 360)}
        assert not any(id(p) in kept_ids for p in serve)


class TestTheRadiusScalesWithFrameHeight:
    @staticmethod
    def _spread_site(spacing: float = 4.0, side: int = 7, visits: int = 3):
        """
        A site whose detections sit on a `spacing`-px grid: no two closer than
        4px, so at 360p (6px radius) each has under STATIC_SITE_MIN_POSITIONS
        neighbours, and at 1080p (18px) each has well over.
        """
        tr = _Track()
        grid = [(SITE[0] + spacing * i, SITE[1] + spacing * j)
                for i in range(side) for j in range(side)]
        dwelt = []
        for v in range(visits):
            dwelt += [tr.add(x, y) for x, y in grid]
            tr.flight(6)
            tr.idle(60.0)
        return tr, dwelt

    def test_the_radius_is_the_reference_radius_times_height_over_360(self):
        assert _static_site_radius(360) == ball.STATIC_SITE_RADIUS_PX
        assert _static_site_radius(1080) == pytest.approx(3 * ball.STATIC_SITE_RADIUS_PX)
        assert _static_site_radius(720) == pytest.approx(2 * ball.STATIC_SITE_RADIUS_PX)

    def test_a_site_spread_over_more_than_the_360p_radius_is_one_only_at_1080p(self):
        tr, dwelt = self._spread_site()
        assert drop_static_sites(tr.positions, 360) == tr.positions
        kept_ids = {id(p) for p in drop_static_sites(tr.positions, 1080)}
        assert not any(id(p) in kept_ids for p in dwelt)

    def test_an_unknown_height_falls_back_to_360p_and_says_so(self, caplog):
        tr, _ = self._spread_site()
        with caplog.at_level("WARNING", logger="ml.pipeline.ball"):
            assert drop_static_sites(tr.positions, 0) == tr.positions
        assert "without a frame height" in caplog.text


class TestTheSwitch:
    def test_off_returns_the_tracker_itself(self):
        tr, _, _ = _site_with_flight_between()
        tracker = TrackedBall(positions=tr.positions, misses=2)
        snapshot = list(tracker.positions)
        assert filter_static_sites(tracker, 360, enabled=False) is tracker
        assert tracker.positions == snapshot

    def test_on_returns_a_new_tracker_without_the_site(self):
        tr, _, moving = _site_with_flight_between()
        tracker = TrackedBall(positions=tr.positions, misses=2)
        out = filter_static_sites(tracker, 360, enabled=True)
        assert out is not tracker
        assert out.positions == moving
        assert out.misses == 2
        assert len(tracker.positions) > len(moving), "the input must not be mutated"

    def test_enabled_must_be_said(self):
        with pytest.raises(TypeError):
            filter_static_sites(TrackedBall(), 360)  # type: ignore[call-arg]

    def test_a_failure_inside_the_filter_degrades_to_the_unfiltered_track(self, monkeypatch, caplog):
        def boom(*_a, **_k):
            raise RuntimeError("filter bug")

        monkeypatch.setattr(ball, "drop_static_sites", boom)
        tracker = TrackedBall(positions=_site_with_flight_between()[0].positions)
        with caplog.at_level("WARNING", logger="ml.pipeline.ball"):
            assert filter_static_sites(tracker, 360, enabled=True) is tracker
        assert "static-site filter failed" in caplog.text

    @pytest.mark.parametrize("entry", ["detect_contacts", "detect_rallies"])
    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_entry_points_filter_before_find_contacts(self, monkeypatch, entry, enabled):
        tr, _, moving = _site_with_flight_between()
        seen = {}

        def fake_find_contacts(tracker, frame_height=0, **kw):
            seen["positions"] = list(tracker.positions)
            return []

        monkeypatch.setattr(ball, "find_contacts", fake_find_contacts)
        monkeypatch.setattr(ball, "track_ball",
                            lambda *a, **k: TrackedBall(positions=list(tr.positions)))
        monkeypatch.setattr(ball, "_read_frame_height", lambda *a, **k: 360)
        monkeypatch.setattr(ball, "contacts_to_rallies", lambda *a, **k: [])
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FPS=5, CAP_PROP_FRAME_COUNT=7, CAP_PROP_FRAME_HEIGHT=4,
            VideoCapture=lambda *_: types.SimpleNamespace(
                get=lambda prop: {5: 30.0, 7: 900.0, 4: 360.0}[prop],
                release=lambda: None,
            ),
        )
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

        getattr(ball, entry)("video.mp4", api_key="k", static_site_filter=enabled)
        assert seen["positions"] == (moving if enabled else tr.positions)

    @pytest.mark.parametrize("entry", ["detect_contacts", "detect_rallies"])
    def test_the_entry_points_default_off(self, entry):
        import inspect

        param = inspect.signature(getattr(ball, entry)).parameters["static_site_filter"]
        assert param.default is False


class TestTheEvalMirror:
    """tune_contacts.apply_static_site_filter: the loaders' half of the switch."""

    @staticmethod
    def _loaded():
        tr, _, moving = _site_with_flight_between()
        dicts = [{"time": p.time, "x": p.x, "y": p.y, "confidence": p.confidence}
                 for p in tr.positions]
        return TrackedBall(positions=tr.positions), dicts, moving

    def test_off_hands_back_the_same_objects(self, monkeypatch):
        from ml.eval import tune_contacts as TC

        track, dicts, _ = self._loaded()
        monkeypatch.setattr(TC, "STATIC_SITE_FILTER", False)
        out_track, out_dicts = TC.apply_static_site_filter(track, dicts, 360)
        assert out_track is track and out_dicts is dicts

    def test_on_keeps_the_dicts_in_step_with_the_positions(self, monkeypatch):
        from ml.eval import tune_contacts as TC

        track, dicts, moving = self._loaded()
        monkeypatch.setattr(TC, "STATIC_SITE_FILTER", True)
        out_track, out_dicts = TC.apply_static_site_filter(track, dicts, 360)
        assert out_track.positions == moving
        assert [(d["time"], d["x"], d["y"]) for d in out_dicts] == [
            (p.time, p.x, p.y) for p in moving]

    def test_an_explicit_position_beats_the_mirror(self, monkeypatch):
        from ml.eval import tune_contacts as TC

        track, dicts, moving = self._loaded()
        monkeypatch.setattr(TC, "STATIC_SITE_FILTER", False)
        assert TC.apply_static_site_filter(track, dicts, 360, enabled=True)[0].positions == moving
        monkeypatch.setattr(TC, "STATIC_SITE_FILTER", True)
        assert TC.apply_static_site_filter(track, dicts, 360, enabled=False)[0] is track
