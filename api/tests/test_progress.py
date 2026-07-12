"""Unit tests for pipeline progress reporting (pure logic, no DB).

Run from api/: pytest tests/test_progress.py
"""
import pytest

from app.workers.progress import GameProgress, compute_stage_spans


# ── compute_stage_spans ──────────────────────────────────────────────────────

@pytest.mark.parametrize("condense", [False, True])
@pytest.mark.parametrize("cache_hit", [False, True])
def test_spans_tile_zero_to_one(condense, cache_hit):
    spans = compute_stage_spans(condense, cache_hit)
    ordered = list(spans.values())
    assert ordered[0][0] == 0.0
    assert ordered[-1][1] == pytest.approx(1.0)
    for (_, prev_end), (next_start, _) in zip(ordered, ordered[1:]):
        assert prev_end == pytest.approx(next_start)
    for start, end in ordered:
        assert end > start


def test_condense_stage_only_when_requested():
    assert "condensing" in compute_stage_spans(condense=True, ball_cache_hit=False)
    assert "condensing" not in compute_stage_spans(condense=False, ball_cache_hit=False)


def _tracking_width(spans):
    start, end = spans["tracking_ball"]
    return end - start


def test_cache_hit_shrinks_tracking_span():
    miss = compute_stage_spans(condense=False, ball_cache_hit=False)
    hit = compute_stage_spans(condense=False, ball_cache_hit=True)
    assert _tracking_width(miss) > 0.5   # tracking dominates a cache-miss run
    assert _tracking_width(hit) < 0.15   # and nearly vanishes on a hit
    # The stages after tracking inherit the freed-up width.
    assert _tracking_width(miss) > 3 * _tracking_width(hit)


def test_modal_tracking_sits_between_cache_hit_and_local():
    local = compute_stage_spans(condense=False, ball_cache_hit=False, modal=False)
    modal = compute_stage_spans(condense=False, ball_cache_hit=False, modal=True)
    hit = compute_stage_spans(condense=False, ball_cache_hit=True, modal=True)
    assert _tracking_width(hit) < _tracking_width(modal) < _tracking_width(local)


def test_cache_hit_wins_over_modal():
    assert compute_stage_spans(True, ball_cache_hit=True, modal=True) == \
        compute_stage_spans(True, ball_cache_hit=True, modal=False)


@pytest.mark.parametrize("modal", [False, True])
def test_spans_still_tile_with_modal(modal):
    spans = compute_stage_spans(condense=True, ball_cache_hit=False, modal=modal)
    ordered = list(spans.values())
    assert ordered[0][0] == 0.0
    assert ordered[-1][1] == pytest.approx(1.0)


# ── GameProgress ─────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


@pytest.fixture()
def harness():
    writes = []
    clock = FakeClock()
    spans = compute_stage_spans(condense=True, ball_cache_hit=False)
    gp = GameProgress(spans, write=lambda p, s: writes.append((p, s)), clock=clock)
    return gp, writes, clock, spans


def test_stage_boundary_always_writes(harness):
    gp, writes, clock, spans = harness
    gp.stage("tracking_ball")
    assert writes[-1] == (pytest.approx(round(spans["tracking_ball"][0], 4)), "tracking_ball")
    # A second boundary writes even with no time elapsed (throttle bypassed).
    gp.stage("scoring_highlights")
    assert writes[-1][1] == "scoring_highlights"


def test_update_maps_fraction_into_stage_span(harness):
    gp, writes, clock, spans = harness
    gp.stage("tracking_ball")
    clock.tick(10)
    gp.update(0.5)
    start, end = spans["tracking_ball"]
    assert writes[-1][0] == pytest.approx(round(start + 0.5 * (end - start), 4))


def test_monotonic_never_decreases(harness):
    gp, writes, clock, spans = harness
    gp.stage("tracking_ball")
    clock.tick(10)
    gp.update(0.9)
    high = writes[-1][0]
    # Modal fallback restarting local tracking reports low fractions again.
    clock.tick(10)
    gp.update(0.1)
    clock.tick(10)
    gp.update(0.2)
    assert all(p >= high for p, _ in writes[writes.index((high, "tracking_ball")):])


def test_throttle_swallows_rapid_small_updates(harness):
    gp, writes, clock, spans = harness
    gp.stage("tracking_ball")
    n = len(writes)
    # Plenty of time elapsed, but the value barely moves: no write.
    clock.tick(10)
    gp.update(0.001)
    assert len(writes) == n
    # Big move but no time elapsed since the last write attempt registered
    # one: first big update writes (time passed), immediate second doesn't.
    gp.update(0.5)
    n = len(writes)
    gp.update(0.6)
    assert len(writes) == n
    clock.tick(3)
    gp.update(0.7)
    assert len(writes) == n + 1


def test_failing_writer_does_not_raise():
    def boom(p, s):
        raise RuntimeError("db down")

    spans = compute_stage_spans(condense=False, ball_cache_hit=False)
    gp = GameProgress(spans, write=boom, clock=FakeClock())
    gp.stage("tracking_ball")  # must not raise
    gp.update(0.5)


def test_unknown_stage_is_ignored(harness):
    gp, writes, clock, spans = harness
    gp.stage("not_a_stage")   # must not raise
    n = len(writes)
    gp.update(0.5)            # no span to map into: no write
    assert len(writes) == n


def test_update_clamps_out_of_range_fractions(harness):
    gp, writes, clock, spans = harness
    gp.stage("tracking_ball")
    clock.tick(10)
    gp.update(5.0)
    assert writes[-1][0] == pytest.approx(round(spans["tracking_ball"][1], 4))
    clock.tick(10)
    gp.update(-3.0)           # clamped to 0, monotonic keeps the high value
    assert writes[-1][0] == pytest.approx(round(spans["tracking_ball"][1], 4))
