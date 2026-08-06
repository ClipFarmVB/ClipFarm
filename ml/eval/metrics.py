"""
Time-based evaluation metrics for clip quality (CF-55) and dead-time removal (CF-98).

Both modes share the interval arithmetic below and differ only in what the two
sides mean: highlight clips against labeled highlights for CF-55, condense keep
windows against labeled ball-in-play spans for CF-98. See the dead-time section
further down for that half.

Pure interval arithmetic. Given a human's ground-truth highlight intervals and
the model's clip windows for the *same* video, compute a small set of
threshold-free signals that can be tracked across model versions. The absolute
numbers matter less than their delta between two tagged runs.

Design: comparison is time-based (merge each side's intervals, intersect,
subtract), NOT clip-to-clip matching — every matching rule has an arbitrary
cliff that silently shapes downstream numbers. Partial credit here is
continuous. The only threshold anywhere is the 0.5 "well-captured" reporting
bucket, which is applied over already-computed per-clip fractions; nothing else
derives from it.

No app, no DB, no I/O imports — mirrors ml/pipeline conventions. The one import
is merge_intervals (also pure) so both sides are normalized into
non-overlapping unions the same way the rest of the pipeline does it.

Signal definitions (CF-55), where:
  H = union of human-labeled intervals
  M = union of all model windows
  W = a single model window
"""
from __future__ import annotations

from dataclasses import dataclass

from ml.pipeline.dead_time import merge_intervals

Interval = tuple[float, float]


# ── low-level interval helpers ─────────────────────────────────────────────

def _length(interval: Interval) -> float:
    return max(0.0, interval[1] - interval[0])


def _overlap(a: Interval, b: Interval) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def total_length(intervals: list[Interval]) -> float:
    return sum(_length(iv) for iv in intervals)


def union(intervals: list[Interval]) -> list[Interval]:
    """Merge into sorted, non-overlapping intervals (touching intervals merge)."""
    return merge_intervals([(float(s), float(e)) for s, e in intervals if e > s])


def intersect_length(a: list[Interval], b: list[Interval]) -> float:
    """
    Total overlap seconds between two interval sets. Each side is unioned first,
    so callers need not pre-merge. Linear two-pointer sweep over the unions.
    """
    ua, ub = union(a), union(b)
    i = j = 0
    total = 0.0
    while i < len(ua) and j < len(ub):
        lo = max(ua[i][0], ub[j][0])
        hi = min(ua[i][1], ub[j][1])
        if hi > lo:
            total += hi - lo
        if ua[i][1] < ub[j][1]:
            i += 1
        else:
            j += 1
    return total


# ── inputs & result types ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelWindow:
    """A single model clip window. score is the pipeline's highlight_score."""
    start: float
    end: float
    score: float | None = None

    @property
    def interval(self) -> Interval:
        return (self.start, self.end)


@dataclass
class CaptureBuckets:
    """Per-human-clip capture counts (fraction f = |h ∩ M| / |h|)."""
    well_captured: int   # f >= 0.5
    butchered: int       # 0 < f < 0.5
    missed: int          # f == 0
    total: int


@dataclass
class IncorrectTime:
    """Model seconds outside the human highlights, decomposed by kind."""
    junk: float          # windows that touch no human clip at all
    lead_slop: float     # seconds before the earliest human clip a window hits
    tail_slop: float     # seconds after the latest human clip a window hits
    bridge: float        # uncovered seconds between human clips a window spans

    @property
    def total(self) -> float:
        return self.junk + self.lead_slop + self.tail_slop + self.bridge


@dataclass
class PerClip:
    """Audit row for one human clip."""
    clip: Interval
    fraction: float                     # |h ∩ M| / |h|
    matched_window_indices: list[int]   # model windows overlapping this clip


@dataclass
class PerWindow:
    """Audit row for one model window; covered + slop kinds sum to its length."""
    window: ModelWindow
    covered: float       # |W ∩ H|
    junk: float
    lead_slop: float
    tail_slop: float
    bridge: float
    is_junk: bool


@dataclass
class EvalSignals:
    captured_pct: float | None    # |M ∩ H| / |H|; None when there are no human clips
    buckets: CaptureBuckets
    incorrect: IncorrectTime
    auc: float | None             # score separation; None if no pos or no neg windows
    human_seconds: float          # |H|
    model_seconds: float          # |M|
    per_clip: list[PerClip]
    per_window: list[PerWindow]


# ── per-window decomposition ───────────────────────────────────────────────

def _decompose_window(window: ModelWindow, human_union: list[Interval]) -> PerWindow:
    """
    Split a model window's seconds into covered / junk / lead / tail / bridge.
    covered + junk + lead + tail + bridge == length(window) (exactly).
    """
    w_interval = window.interval
    covered = intersect_length([w_interval], human_union)
    if covered <= 0.0:
        return PerWindow(
            window, covered=0.0, junk=_length(w_interval),
            lead_slop=0.0, tail_slop=0.0, bridge=0.0, is_junk=True,
        )

    ws, we = window.start, window.end
    hit = [h for h in human_union if _overlap(w_interval, h) > 0.0]
    earliest_start = min(h[0] for h in hit)
    latest_end = max(h[1] for h in hit)

    lead_slop = max(0.0, min(earliest_start, we) - ws)
    tail_slop = max(0.0, we - max(latest_end, ws))
    mid_len = _length((max(ws, earliest_start), min(we, latest_end)))
    bridge = max(0.0, mid_len - covered)
    return PerWindow(
        window, covered=covered, junk=0.0,
        lead_slop=lead_slop, tail_slop=tail_slop, bridge=bridge, is_junk=False,
    )


def _auc(per_window: list[PerWindow]) -> float | None:
    """
    Probability a highlight-overlapping window outscores a junk window
    (ties count 0.5). None if either class is empty or any score is missing.
    """
    pos = [pw.window.score for pw in per_window if not pw.is_junk]
    neg = [pw.window.score for pw in per_window if pw.is_junk]
    if not pos or not neg:
        return None
    if any(s is None for s in pos) or any(s is None for s in neg):
        return None
    pos_f = [s for s in pos if s is not None]
    neg_f = [s for s in neg if s is not None]

    wins = 0.0
    for p in pos_f:
        for n in neg_f:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos_f) * len(neg_f))


# ── public entry point ─────────────────────────────────────────────────────

def evaluate(
    human_clips: list[Interval],
    model_windows: list[ModelWindow],
) -> EvalSignals:
    """Compute all CF-55 signals for one set of human clips vs one model run."""
    human_norm: list[Interval] = [(float(s), float(e)) for s, e in human_clips]
    human_union = union(human_norm)
    model_union = union([w.interval for w in model_windows])

    human_seconds = total_length(human_union)
    model_seconds = total_length(model_union)

    inter = intersect_length(model_union, human_union)
    captured_pct = (inter / human_seconds) if human_seconds > 0.0 else None

    # Buckets and audit rows are per *individual* human clip, not the union.
    per_clip: list[PerClip] = []
    well = butchered = missed = 0
    for clip in human_norm:
        length = _length(clip)
        fraction = (intersect_length([clip], model_union) / length) if length > 0.0 else 0.0
        matched = [
            i for i, w in enumerate(model_windows) if _overlap(clip, w.interval) > 0.0
        ]
        per_clip.append(PerClip(clip=clip, fraction=fraction, matched_window_indices=matched))
        if fraction <= 0.0:
            missed += 1
        elif fraction >= 0.5:
            well += 1
        else:
            butchered += 1
    buckets = CaptureBuckets(well, butchered, missed, total=len(human_norm))

    per_window = [_decompose_window(w, human_union) for w in model_windows]
    incorrect = IncorrectTime(
        junk=sum(pw.junk for pw in per_window),
        lead_slop=sum(pw.lead_slop for pw in per_window),
        tail_slop=sum(pw.tail_slop for pw in per_window),
        bridge=sum(pw.bridge for pw in per_window),
    )

    return EvalSignals(
        captured_pct=captured_pct,
        buckets=buckets,
        incorrect=incorrect,
        auc=_auc(per_window),
        human_seconds=human_seconds,
        model_seconds=model_seconds,
        per_clip=per_clip,
        per_window=per_window,
    )


# ── dead-time metrics (CF-98) ──────────────────────────────────────────────
#
# The condense flow (ml/pipeline/dead_time.py) derives *keep-windows* (rallies);
# everything between them is cut as dead time. Ground truth is human-labeled
# *keep* (rally) regions; dead time is their complement within [0, duration].
# Same interval arithmetic as above — we just need the actual overlap/subtract
# spans (not only their totals) so the audit can name every divergence.


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Overlap of two interval sets as concrete spans. Each side unioned first."""
    ua, ub = union(a), union(b)
    i = j = 0
    out: list[Interval] = []
    while i < len(ua) and j < len(ub):
        lo = max(ua[i][0], ub[j][0])
        hi = min(ua[i][1], ub[j][1])
        if hi > lo:
            out.append((lo, hi))
        if ua[i][1] < ub[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """a minus b — the parts of a not covered by any interval in b."""
    ua, ub = union(a), union(b)
    out: list[Interval] = []
    for s, e in ua:
        cur = s
        for bs, be in ub:
            if be <= cur:
                continue
            if bs >= e:
                break
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


@dataclass
class DeadTimeSignals:
    """Dead-time / condense quality for one run (CF-98).

    Two headline numbers that trade off against each other, plus the itemized
    audit of *where* the run diverged from the human labels.
    """
    # Aggressiveness: of the true dead time, how much did we cut? Higher = tighter.
    dead_removed_pct: float | None
    # Harm (the one that matters): real play we cut, in seconds and as % of play.
    live_removed_sec: float
    live_removed_pct: float | None
    kept_play_pct: float | None          # recall = 1 - live_removed_pct
    condense_ratio: float | None         # |model_keep| / duration
    human_keep_sec: float
    human_dead_sec: float
    model_keep_sec: float
    duration: float
    # Audit spans, each sorted longest-first:
    missed_dead: list[Interval]          # dead time kept when it should've been cut
    over_cut_live: list[Interval]        # real play removed when it should've been kept


def evaluate_deadtime(
    human_keep: list[Interval],
    model_keep: list[Interval],
    duration: float,
) -> DeadTimeSignals:
    """Score one condense run's keep-windows against human rally regions.

    human_keep / model_keep are *keep* (in-play) spans; dead time is the
    complement within [0, duration]. Inputs are clamped to that span so a stray
    label past the video end can't skew the totals.
    """
    span: list[Interval] = [(0.0, float(duration))]
    hk = intersect(human_keep, span)          # clamp to [0, duration]
    mk = intersect(model_keep, span)
    human_dead = subtract(span, hk)
    model_cut = subtract(span, mk)

    human_keep_sec = total_length(hk)
    human_dead_sec = total_length(human_dead)
    model_keep_sec = total_length(mk)

    # dead time we kept (shortfall from 100% removal) and play we cut (the harm)
    missed_dead = intersect(human_dead, mk)
    over_cut_live = intersect(hk, model_cut)

    dead_removed = total_length(intersect(model_cut, human_dead))
    dead_removed_pct = (dead_removed / human_dead_sec) if human_dead_sec > 0.0 else None
    live_removed_sec = total_length(over_cut_live)
    live_removed_pct = (live_removed_sec / human_keep_sec) if human_keep_sec > 0.0 else None
    kept_play_pct = (1.0 - live_removed_pct) if live_removed_pct is not None else None
    condense_ratio = (model_keep_sec / duration) if duration > 0.0 else None

    by_len = lambda iv: iv[0] - iv[1]  # noqa: E731 — sort longest-first
    return DeadTimeSignals(
        dead_removed_pct=dead_removed_pct,
        live_removed_sec=live_removed_sec,
        live_removed_pct=live_removed_pct,
        kept_play_pct=kept_play_pct,
        condense_ratio=condense_ratio,
        human_keep_sec=human_keep_sec,
        human_dead_sec=human_dead_sec,
        model_keep_sec=model_keep_sec,
        duration=float(duration),
        missed_dead=sorted(missed_dead, key=by_len),
        over_cut_live=sorted(over_cut_live, key=by_len),
    )
