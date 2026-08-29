"""
Shared interval primitives.

A time span is a (start, end) pair in seconds. Several parts of the system
normalize spans into a non-overlapping union before comparing or measuring
them, and they must all do it the same way or the numbers stop meaning the
same thing.

Its own module rather than a corner of dead_time.py (CF-95): ml/eval/metrics.py
documents itself as having no app, no DB and no I/O imports, and reached into a
pipeline module for merge_intervals to keep that promise. dead_time.py happens
to be stdlib-only today, but nothing obliges it to stay that way — it derives
keep windows and is free to grow a dependency. This module is the thing that is
obliged.

Nothing is imported here on purpose, and it should stay that way: anything
reachable from metrics.py has to work without numpy or cv2.

**CI does not enforce that, so a test does.** An earlier version of this
paragraph said the eval unit tests "run in CI with only ruff, mypy and pytest
installed". They do not — `.github/workflows/ci.yml` pins and installs
`numpy==1.26.4` immediately before `python -m pytest ml/tests/`, deliberately
and with its own comment saying why. Only `ruff check ml/eval` and
`mypy ml/eval` run numpy-free, and neither executes an import, so nothing in CI
was ever positioned to notice a heavy import here. `ml/tests/test_intervals.py`
manufactures that environment instead, and its control asserts the blocker can
fail before trusting that it did.
"""
from __future__ import annotations

Interval = tuple[float, float]


def merge_intervals(intervals: list[Interval], merge_gap_seconds: float = 0.0) -> list[Interval]:
    """Sort intervals and merge any pair closer than merge_gap_seconds."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[Interval] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= merge_gap_seconds:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
