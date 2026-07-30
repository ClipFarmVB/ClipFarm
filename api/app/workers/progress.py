"""
Pipeline progress reporting for process_game_task.

Pure logic (no DB imports): the worker injects a write callable over
sync_set_game_progress. Kept separate so the span math, monotonic clamp,
and throttling are unit-testable without a database.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

# Relative cost of each pipeline stage (arbitrary units, normalized into
# cumulative 0-1 spans below). Calibrated from measured per-stage wall-clock
# across three games (8/10/41 min) in the Modal-tracking, CLIP-off regime.
# The per-unit rates behind these numbers:
#   tracking (Modal)  ~16-19 s / video-minute
#   refining (pose)   ~5.6 s / rally
#   cutting           ~8-12 s / clip
#   condensing        ~0.4-0.6 s / kept-second
# This base table is the LOCAL-CPU regime (tracking_ball is the slow path);
# cache-hit and Modal override tracking_ball below.
#
# ORDER IS LOAD-BEARING: compute_stage_spans tiles cumulative spans by iterating
# this dict, so the key order must match the pipeline's stage() call sequence in
# process_game_task. Reordering keys silently mis-maps every stage — do not sort.
# test_base_weights_match_pipeline_call_order pins this.
_BASE_WEIGHTS: dict[str, float] = {
    "downloading": 3.0,
    # 2-4 s regardless of video length (~0.1% of a run).
    "analyzing_audio": 1.0,
    # Local-CPU tracking (~1.4x realtime, CF-11) — roughly 5x the Modal cost
    # below. Extrapolated from the Modal measurement, not measured directly.
    "tracking_ball": 135.0,
    # ~0 with CLIP off (the deployment default). If clip_verify_enabled is
    # turned on, this stage runs CLIP per rally and must be re-measured/re-weighted.
    "scoring_highlights": 1.0,
    "refining_actions": 12.0,
    "cutting_clips": 21.0,
    # HIGHLY variable (29-43% across the sample games): condensing tracks
    # kept-seconds, not video duration, so any static weight mis-sizes games
    # with lots or little dead time. Proper fix: size this span from
    # sum(window durations) at runtime once the keep-windows are known.
    "condensing": 35.0,
}

# A cache hit turns tracking into a seconds-long download of cached positions;
# a couple of units keeps it a sliver of the bar.
_CACHE_HIT_TRACKING_WEIGHT = 2.0

# Modal GPU tracking (CF-11): measured ~16-19 s per minute of video, i.e.
# ~27-30% of a condense-on run — far more than the old guess of 12.
_MODAL_TRACKING_WEIGHT = 27.0

# Condensing re-encodes only the kept footage; measured ~0.41-0.61 s of encode
# per second kept. Used to re-price the condensing span once the keep-windows
# (and thus kept-seconds) are known at runtime — see rebudget_final_stage.
CONDENSE_SECS_PER_KEPT_SEC = 0.5

# Only report on a meaningful change: the frontend polls every ~5s, so
# sub-percent or sub-2s writes are pure DB churn.
_MIN_DELTA = 0.01
_MIN_INTERVAL_SECONDS = 2.0


def compute_stage_spans(
    condense: bool, ball_cache_hit: bool, modal: bool = False
) -> dict[str, tuple[float, float]]:
    """Map each stage name to its cumulative (start, end) slice of 0-1."""
    weights = dict(_BASE_WEIGHTS)
    if not condense:
        weights.pop("condensing")
    if ball_cache_hit:
        weights["tracking_ball"] = _CACHE_HIT_TRACKING_WEIGHT
    elif modal:
        weights["tracking_ball"] = _MODAL_TRACKING_WEIGHT

    total = sum(weights.values())
    spans: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    for name, weight in weights.items():
        spans[name] = (cursor, cursor + weight / total)
        cursor += weight / total
    return spans


class GameProgress:
    """
    Maps within-stage fractions onto the overall 0-1 bar and writes them out.

    Guarantees the pipeline can rely on:
    - monotonic: the reported value never decreases (the Modal -> local-CPU
      tracking fallback restarts work; the bar must not rewind)
    - throttled: writes only on >=1pp change AND >=2s elapsed (forced writes
      at stage boundaries and re-budgets bypass both)
    - non-fatal: a failing writer logs and is otherwise ignored
    """

    def __init__(
        self,
        spans: dict[str, tuple[float, float]],
        write: Callable[[float, str | None], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._spans = spans
        self._write = write
        self._clock = clock
        self._start = clock()  # for runtime span re-pricing (rebudget_final_stage)
        self._stage: str | None = None
        self._value = 0.0
        self._last_written = -1.0
        self._last_write_at = float("-inf")

    def stage(self, name: str) -> None:
        """Enter a stage; reports its span start. Stage boundaries always write."""
        self._stage = name
        if name not in self._spans:
            logger.warning("Unknown progress stage %r — bar will hold position", name)
            return
        self._advance(self._spans[name][0], force=True)

    def update(self, fraction: float) -> None:
        """Report progress within the current stage (fraction 0-1)."""
        if self._stage is None or self._stage not in self._spans:
            return
        start, end = self._spans[self._stage]
        fraction = min(max(fraction, 0.0), 1.0)
        self._advance(start + fraction * (end - start))

    def rebudget_final_stage(self, name: str, est_seconds: float) -> None:
        """Re-price the final stage's span from a runtime cost estimate.

        The static weights guess each stage's share up front, but condensing's
        cost only becomes knowable mid-run — once the keep-windows exist, its
        time is ~kept_seconds * CONDENSE_SECS_PER_KEPT_SEC. Call this on entering
        that stage to re-tile [position, 1.0] so it occupies a share matching
        `est_seconds` against the wall-clock already elapsed.

        Forward-only: it can shrink an over-reserved final stage (jumping the
        bar ahead to the true boundary) but never rewinds, so an under-reserved
        stage just fills whatever bar remains. Because it uses elapsed time, it
        needs `name` to be the last, still-unfinished stage.
        """
        if name not in self._spans:
            return
        elapsed = max(self._clock() - self._start, 1e-6)
        ideal_start = elapsed / (elapsed + max(est_seconds, 0.0))
        _, end = self._spans[name]
        new_start = min(max(ideal_start, self._value), end)
        self._spans[name] = (new_start, end)
        self._advance(new_start, force=True)

    def _advance(self, value: float, force: bool = False) -> None:
        self._value = max(self._value, value)
        now = self._clock()
        significant = self._value - self._last_written >= _MIN_DELTA
        rested = now - self._last_write_at >= _MIN_INTERVAL_SECONDS
        if not force and not (significant and rested):
            return
        try:
            self._write(round(self._value, 4), self._stage)
        except Exception:
            # Leave _last_written untouched so the next significant move retries
            # the write instead of recording a value that never landed — but do
            # bookkeep the attempt time, so a sustained outage stays throttled
            # rather than re-attempting (and logging) on every update().
            logger.warning("Progress write failed — continuing", exc_info=True)
            self._last_write_at = now
            return
        self._last_written = self._value
        self._last_write_at = now
