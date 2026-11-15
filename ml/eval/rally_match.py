"""
Per-rally scoring of dead-time keep-windows (CF-604).

Every dead-time number elsewhere is seconds-weighted, and seconds hide the
error that matters most: deleting a short rally whole. A 2s serve cut entirely
costs the same 2s as trimming a 20s rally, yet only one of them loses a point
from the condensed video. This module scores keep-windows rally by rally.

**A rally is one span of `union(human_keep)`** — one human-labelled in-play
span of the fixture, merged the way `metrics.evaluate_deadtime` merges them.
There is no matching rule to tune: a keep-window is not paired to a rally, the
rally's own seconds are intersected with the windows, so the only thresholds
are the reporting ones below (50% / 100% kept, and the length buckets).

Kept small and pure on purpose, so CF-593's rally matcher
(`ml/eval/rally_match.py` on its branch, not merged when this was written) can
replace it: callers use `match_rallies`, `summarize` and `units` only.

No app, no I/O — interval arithmetic over `ml.eval.metrics`.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from ml.eval.metrics import intersect, intersect_length, union
from ml.pipeline.intervals import Interval

# The reporting buckets of whole-rally deletions, by the rally's labelled
# length: (name, upper bound in seconds, inclusive). The last is unbounded.
LENGTH_BUCKETS: tuple[tuple[str, float], ...] = (
    ("le_3s", 3.0),
    ("3_8s", 8.0),
    ("gt_8s", float("inf")),
)
# Below this many seconds kept a rally counts as deleted whole, and within it of
# its length as kept whole: float slop from window arithmetic, not a tolerance.
EPS = 1e-6


@dataclass(frozen=True)
class RallyMatch:
    """One labelled rally against one set of keep-windows."""
    start: float
    end: float
    kept_sec: float
    # Signed, in seconds, from the windows that touch the rally, clipped to its
    # territory (`territories`). start_err > 0: the rally's start was cut;
    # < 0: lead-in dead time kept. end_err < 0: the end was cut; > 0: tail
    # kept. None when the rally is deleted whole.
    start_err: float | None
    end_err: float | None

    @property
    def length(self) -> float:
        return self.end - self.start

    @property
    def kept_frac(self) -> float:
        return self.kept_sec / self.length if self.length > 0 else 0.0

    @property
    def deleted(self) -> bool:
        return self.kept_sec <= EPS

    @property
    def kept_whole(self) -> bool:
        return self.kept_sec >= self.length - EPS


def rallies(human_keep: list[Interval], duration: float) -> list[Interval]:
    """The fixture's rallies: union(human_keep) clamped to [0, duration]."""
    return intersect(human_keep, [(0.0, float(duration))])


def territories(spans: list[Interval], duration: float) -> list[Interval]:
    """Each rally's neighbourhood: from the midpoint of the gap before it to the
    midpoint of the gap after, the first and last running to the video's ends.
    A keep-window that touches the rally is read only inside it, so one window
    merged across two rallies does not book the first rally's start as the
    second's lead-in."""
    out = []
    for i, (s, e) in enumerate(spans):
        lo = 0.0 if i == 0 else (spans[i - 1][1] + s) / 2.0
        hi = float(duration) if i == len(spans) - 1 else (e + spans[i + 1][0]) / 2.0
        out.append((lo, hi))
    return out


def match_rallies(human_keep: list[Interval], windows: list[Interval],
                  duration: float) -> list[RallyMatch]:
    """Every rally of `human_keep`, scored against `windows`, in time order."""
    spans = rallies(human_keep, duration)
    keep = union(windows)
    out = []
    for (s, e), terr in zip(spans, territories(spans, duration)):
        kept = intersect_length([(s, e)], keep)
        start_err = end_err = None
        if kept > EPS:
            touching = [w for w in keep if min(w[1], e) > max(w[0], s)]
            near = intersect(touching, [terr])
            start_err, end_err = near[0][0] - s, near[-1][1] - e
        out.append(RallyMatch(s, e, kept, start_err, end_err))
    return out


def bucket(length: float) -> str:
    for name, upper in LENGTH_BUCKETS:
        if length <= upper:
            return name
    raise AssertionError("the last bucket is unbounded")


def _median(xs: list[float]) -> float | None:
    return float(statistics.median(xs)) if xs else None


def summarize(matches: list[RallyMatch]) -> dict:
    """The per-rally block of a fold record.

    recall_50 / recall_100: the share of rallies with at least half / all of
    their seconds kept (None with no rallies). deleted: rallies kept for 0s,
    by length bucket. The errors are medians over the rallies not deleted.
    """
    n = len(matches)
    starts = [m.start_err for m in matches if m.start_err is not None]
    ends = [m.end_err for m in matches if m.end_err is not None]
    deleted = {name: 0 for name, _ in LENGTH_BUCKETS}
    for m in matches:
        if m.deleted:
            deleted[bucket(m.length)] += 1
    return {
        "rallies": n,
        "recall_50": sum(m.kept_frac >= 0.5 for m in matches) / n if n else None,
        "recall_100": sum(m.kept_whole for m in matches) / n if n else None,
        "deleted": deleted,
        "start_err_median_sec": _median(starts),
        "start_abs_err_median_sec": _median([abs(x) for x in starts]),
        "end_err_median_sec": _median(ends),
        "end_abs_err_median_sec": _median([abs(x) for x in ends]),
    }


def units(human_keep: list[Interval], duration: float) -> list[Interval]:
    """The block-bootstrap units: each rally plus the gap that follows it, the
    lead-in before the first rally folded into the first. They tile
    [0, duration], so a quantity summed over them is the game's total."""
    spans = rallies(human_keep, duration)
    if not spans:
        return [(0.0, float(duration))]
    starts = [0.0] + [s for s, _ in spans[1:]]
    ends = [s for s, _ in spans[1:]] + [float(duration)]
    return list(zip(starts, ends))
