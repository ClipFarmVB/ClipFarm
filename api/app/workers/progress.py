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
# cumulative 0-1 spans below). Tracking dominates a cache-miss run; condense
# re-encodes kept footage at roughly realtime.
_BASE_WEIGHTS: dict[str, float] = {
    "downloading": 4.0,
    "analyzing_audio": 2.0,
    "tracking_ball": 60.0,
    "scoring_highlights": 6.0,
    "refining_actions": 8.0,
    "cutting_clips": 8.0,
    "condensing": 25.0,
}

# A cache hit turns ~30 min of tracking into a download of cached positions;
# leaving it 60 units would park the bar at ~80% while the genuinely slow
# stages (pose, cutting) share the sliver that remains.
_CACHE_HIT_TRACKING_WEIGHT = 2.0

# Modal GPU tracking (CF-11) runs in low single-digit minutes vs ~1.4x video
# duration on local CPU — still the longest stage, but nowhere near 60 units.
_MODAL_TRACKING_WEIGHT = 12.0

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
    - throttled: writes only on >=1pp change or >=2s elapsed
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
            logger.warning("Progress write failed — continuing", exc_info=True)
        self._last_written = self._value
        self._last_write_at = now
