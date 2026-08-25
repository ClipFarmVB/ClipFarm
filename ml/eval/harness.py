"""
Evaluation harness (CF-55): score a model run against a ground-truth fixture
and append a tagged row to the results log so improvement is tracked across
versions.

metrics.py holds the pure math; this module does the impure edges — loading
fixtures, acquiring the model's windows, printing the report, and writing
results. Any DB/R2/pipeline access stays behind lazy imports so `import
ml.eval.metrics` never drags in the app.

Model-window acquisition modes:
  --clips-json PATH   highlight: read pre-gate and post-gate windows from a
                      dumped JSON {"pre_gate": [{start,end,highlight_score}], ...}
  --windows-json PATH deadtime: read model keep-windows from {"keep": [...]}
  --offline           re-run the pipeline stages against the fixture's source
                      video, loading ball positions from the R2 ball-cache.
                      In deadtime mode this derives keep-windows via
                      dead_time.py, exactly as the condense stage does.

Usage:
  python -m ml.eval.harness --test test1 --version <label> --clips-json dump.json
  python -m ml.eval.harness --mode deadtime --test test1 --version <label> --offline
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ml.eval.metrics import (
    DeadTimeSignals,
    EvalSignals,
    IncorrectTime,
    ModelWindow,
    evaluate,
    evaluate_deadtime,
)
from ml.pipeline.intervals import Interval

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVAL_DIR / "fixtures"
RESULTS_DIR = EVAL_DIR / "results"

# Module constants worth snapshotting alongside each run — a version tag is
# meaningless later without the settings that produced it.
_SNAPSHOT_MODULES = ("ml.pipeline.ball", "ml.pipeline.score")


# ── timestamp / fixture loading ────────────────────────────────────────────

def parse_timestamp(value: str | int | float) -> float:
    """Accept mm:ss, hh:mm:ss, or raw seconds → float seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(p) for p in str(value).split(":")]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Unrecognized timestamp: {value!r}")


@dataclass
class Fixture:
    test_id: str
    clips: list[Interval]
    video_duration_sec: float | None
    raw: dict


def load_fixture(test_id: str) -> Fixture:
    path = FIXTURES_DIR / f"{test_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # Score only the tiers the fixture declares as ground truth. A clip tagged
    # with an excluded tier (no-clip / break / outlier) stays in the file for the
    # labelling record but must not count against the model. Absent key, or a
    # clip with no tier, scores everything — being permissive beats silently
    # dropping labelled data.
    tiers = data.get("ground_truth_tiers")
    clips: list[Interval] = [
        (parse_timestamp(c["start"]), parse_timestamp(c["end"]))
        for c in data["clips"]
        if tiers is None or c.get("tier") is None or c["tier"] in tiers
    ]
    return Fixture(
        test_id=data["test_id"],
        clips=clips,
        video_duration_sec=data.get("video_duration_sec"),
        raw=data,
    )


def _windows_from(items: list[dict]) -> list[ModelWindow]:
    out: list[ModelWindow] = []
    for it in items:
        score = it.get("highlight_score", it.get("score"))
        out.append(ModelWindow(parse_timestamp(it["start"]), parse_timestamp(it["end"]), score))
    return out


def load_clips_json(path: Path) -> tuple[list[ModelWindow], list[ModelWindow]]:
    """Load {pre_gate, post_gate} window lists from a dumped JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return _windows_from(data.get("pre_gate", [])), _windows_from(data.get("post_gate", []))


# ── reporting ──────────────────────────────────────────────────────────────

def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:5.1f}%"


def _fmt_auc(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_signals(title: str, s: EvalSignals) -> str:
    b, inc = s.buckets, s.incorrect
    return "\n".join([
        f"== {title} ==",
        f"  Captured %:     {_fmt_pct(s.captured_pct)}   "
        f"({s.human_seconds:.0f}s labeled, {s.model_seconds:.0f}s clipped)",
        f"  Play buckets:   {b.well_captured} well / {b.butchered} butchered / {b.missed} missed   (of {b.total})",
        f"  Incorrect time: junk {inc.junk:.1f}s | lead {inc.lead_slop:.1f}s | "
        f"tail {inc.tail_slop:.1f}s | bridge {inc.bridge:.1f}s   (total {inc.total:.1f}s)",
        f"  Score AUC:      {_fmt_auc(s.auc)}",
    ])


def format_clip_audit(fixture: Fixture, s: EvalSignals) -> str:
    """List the human clips the model handled worst (missed, then butchered)."""
    rows = []
    for pc in s.per_clip:
        if pc.fraction < 0.5:
            tag = "MISSED   " if pc.fraction <= 0.0 else "butchered"
            start = _seconds_to_ts(pc.clip[0])
            rows.append(f"    {tag}  {start}  ({pc.fraction * 100:.0f}% captured)")
    if not rows:
        return "  All labeled clips well-captured (>=50%)."
    return "  Under-captured clips:\n" + "\n".join(rows)


def _seconds_to_ts(seconds: float) -> str:
    """mm:ss, rolling over to h:mm:ss past an hour — the format the labels use,
    and what a video player's seek bar shows (60:16 is findable nowhere)."""
    total = int(round(seconds))
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


# ── results log ────────────────────────────────────────────────────────────

def _git_commit() -> str | None:
    # Env override lets an in-container run (no .git mounted) record the host commit.
    env = os.environ.get("GIT_COMMIT")
    if env:
        return env
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(EVAL_DIR), stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return None


def config_snapshot() -> dict:
    """Best-effort snapshot of the numeric constants that shape a run."""
    snap: dict[str, dict] = {}
    for name in _SNAPSHOT_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        snap[name] = {
            attr: getattr(mod, attr)
            for attr in dir(mod)
            if attr.isupper()
            and isinstance(getattr(mod, attr), (int, float))
            and not isinstance(getattr(mod, attr), bool)
        }
    # App-level knobs — the gate threshold decides every highlight number, and
    # the condense_* tunables decide every dead-time number (CF-98), so a row
    # without them can't be compared across tuning changes. Collected by prefix
    # off the settings fields, which also keeps credentials out of the log.
    # Lazy + best-effort: --clips-json/--windows-json runs on hosts without the
    # app installed still work (the key is just absent there).
    try:
        from app.config import settings
        app_snap = {
            "highlight_score_threshold": settings.highlight_score_threshold,
            "clip_verify_enabled": settings.clip_verify_enabled,
        }
        fields = getattr(type(settings), "model_fields", None) or getattr(type(settings), "__fields__", {})
        app_snap.update({
            name: getattr(settings, name) for name in fields if name.startswith("condense_")
        })
        snap["app.config"] = app_snap
    except Exception:
        pass
    return snap


# results/*.jsonl is committed so runs can be diffed across versions, and raw
# float tails defeat that: `59.99999999999943` vs `60.00000000000012` is the
# same number twice and reads as a change (CF-94). 4dp is 0.01% on the ratios
# and 0.1 ms on the seconds — finer than anything either measures.
#
# The interval lists below stay at 3dp. Different unit, different natural
# floor: 1 ms is already well under a frame at any frame rate this pipeline
# sees, so more digits there would be recording jitter, not signal.
RESULT_FLOAT_PLACES = 4


def _round(value: float | None, places: int = RESULT_FLOAT_PLACES) -> float | None:
    """`round`, but None survives.

    captured_pct, auc, dead_removed_pct and the rest are legitimately None —
    no human clips, or no positive/negative windows to separate — and round()
    raises TypeError on None rather than passing it through.
    """
    return None if value is None else round(value, places)


def _incorrect_seconds_to_dict(t: IncorrectTime) -> dict:
    """The four parts, rounded, plus a `total` that is their sum.

    `IncorrectTime.total` is a property over the *raw* parts, so rounding it
    directly lets a written row disagree with itself: 60.0 + 5.0 + 11.8333 +
    2.8333 is 79.6666 beside a written total of 79.6667. Nothing reads these
    files back programmatically — humans diff them across runs, and a row whose
    parts do not add up is exactly what misleads that reader. `_decompose_window`
    documents the decomposition as exact, so the file should show it that way.

    Summing the rounded parts moves `total` by at most four half-ulps at 4dp —
    4 x 5e-5, so **2e-4 s**, not 5e-5; the first version of this comment said
    the latter. Still three orders of magnitude below anything this metric
    means, and well inside the 1e-3 that
    `test_rounding_does_not_move_a_value_meaningfully` allows. The outer round
    is there because adding floats reintroduces a tail of its own: without it
    the fixture writes 79.66659999999999.
    """
    # `round`, not `_round`: these four are `float` on IncorrectTime and never
    # None, and the type has to say so for `sum` below to typecheck.
    parts: dict[str, float] = {
        "junk": round(t.junk, RESULT_FLOAT_PLACES),
        "lead_slop": round(t.lead_slop, RESULT_FLOAT_PLACES),
        "tail_slop": round(t.tail_slop, RESULT_FLOAT_PLACES),
        "bridge": round(t.bridge, RESULT_FLOAT_PLACES),
    }
    return {**parts, "total": round(sum(parts.values()), RESULT_FLOAT_PLACES)}


def _signals_to_dict(s: EvalSignals) -> dict:
    return {
        "captured_pct": _round(s.captured_pct),
        # Counts, not measurements — left alone. Rounding an int is a no-op
        # that invites the next reader to wonder what it is guarding against.
        "buckets": {
            "well_captured": s.buckets.well_captured,
            "butchered": s.buckets.butchered,
            "missed": s.buckets.missed,
            "total": s.buckets.total,
        },
        "incorrect_seconds": _incorrect_seconds_to_dict(s.incorrect),
        "auc": _round(s.auc),
        "human_seconds": _round(s.human_seconds),
        "model_seconds": _round(s.model_seconds),
    }


def append_result(
    test_id: str,
    version: str,
    pre: EvalSignals,
    post: EvalSignals,
) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version_tag": version,
        "git_commit": _git_commit(),
        "config_snapshot": config_snapshot(),
        "pre_gate": _signals_to_dict(pre),
        "post_gate": _signals_to_dict(post),
    }
    path = RESULTS_DIR / f"{test_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return path


# ── orchestration ──────────────────────────────────────────────────────────

def run(
    test_id: str,
    version: str,
    pre_windows: list[ModelWindow],
    post_windows: list[ModelWindow],
    *,
    record: bool = True,
) -> tuple[EvalSignals, EvalSignals]:
    fixture = load_fixture(test_id)
    pre = evaluate(fixture.clips, pre_windows)
    post = evaluate(fixture.clips, post_windows)

    print(f"\nEvaluation - test={test_id}  version={version}")
    print(f"Ground truth: {len(fixture.clips)} human clips\n")
    print(format_signals("PRE-GATE (all detected rallies)", pre))
    print()
    print(format_signals("POST-GATE (survived highlight gate)", post))
    print()
    print(format_clip_audit(fixture, post))

    if record:
        path = append_result(test_id, version, pre, post)
        print(f"\nAppended result -> {path}")
    return pre, post


def _require_r2_key(raw: dict, test_id: str) -> str:
    """
    The fixture's source video in R2, or a readable exit.

    Not every fixture has one: a video labeled from a local file (test5) is
    pinned by md5 and duration only, and --offline has nothing to download. That
    is a documented state, so say so instead of dying on a bare KeyError.
    """
    r2_key = raw.get("source_r2_key")
    if not r2_key:
        raise SystemExit(
            f"{test_id}: fixture has no source_r2_key, so --offline has no video to "
            "fetch (the clip was labeled from a local file and never uploaded). "
            "Score a dumped keep-window list with --windows-json instead, or run "
            "python -m ml.eval.visualize_deadtime, which reads ml/eval/ball_caches/."
        )
    return r2_key
def _assert_declared_frame_height(
    fixture, frame_h: int, test_id: str, r2_key: str, mode: str
) -> None:
    """
    Refuse to score if the decoded source is not the height the fixture declares.

    CF-174 made the contact thresholds scale with frame height, so a source that
    decodes at a different resolution is scored against thresholds the fixture
    never meant — and the numbers still look plausible, which is what makes it
    worth failing over.

    This is the ONLY runtime check that the source is the labeled one.
    `source_video_md5` is pinned in every fixture but verified nowhere at
    runtime — only fixture-to-fixture in `test_eval_fixtures.py` — so without
    this a re-encode that changed resolution would shift every threshold
    silently and still produce a recorded row.

    Opt-in per fixture: absent `source_frame_height` skips the check rather than
    failing, so fixtures written before the key existed still run.
    """
    declared = fixture.raw.get("source_frame_height")
    if declared is None or int(declared) == frame_h:
        return
    raise SystemExit(
        f"Offline {mode}: {test_id} declares source_frame_height={int(declared)} "
        f"but {r2_key} decodes at {frame_h}px. CF-174 thresholds scale with frame "
        "height, so this run would not be comparable to the fixture's recorded "
        "numbers. Re-upload the labeled file, or re-label against this one."
    )


def _run_offline(test_id: str) -> tuple[list[ModelWindow], list[ModelWindow]]:
    """
    Re-run the detection + scoring stages against the fixture's source video,
    mirroring process_game_task stages 0-2. Ball positions load from the R2
    cache (free unless model/sample-rate changed), so no re-tracking. Returns
    (pre_gate = all scored rallies, post_gate = those above the gate).

    Runs impure edges (R2, ffmpeg, cv2, app config) behind lazy imports — must
    be invoked where those deps live — the `eval` service, not `worker`,
    which carries production's resource limits (CF-241).
    """
    import tempfile

    import cv2

    from app.config import settings
    from app.services import storage as s3
    from app.workers.tasks import _track_ball_cached
    from ml.pipeline.audio import compute_audio_energy, score_cheers
    from ml.pipeline.ball import contacts_to_rallies, find_contacts
    from ml.pipeline.score import score_highlights

    fixture = load_fixture(test_id)
    r2_key = _require_r2_key(fixture.raw, test_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        local = tmp / "game.mp4"
        print(f"Downloading {r2_key} from R2...")
        s3.download_file(r2_key, local)

        cap = cv2.VideoCapture(str(local))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        duration = n_frames / fps

        # Same guard as the deadtime path: this mode scales the same thresholds.
        _assert_declared_frame_height(fixture, frame_h, test_id, r2_key, "highlight")

        audio = compute_audio_energy(str(local))

        sample_every = max(1, round(fps / 3.0))  # matches process_game_task
        # Pass r2_key: without it the ball-cache lookup and the Modal GPU path are
        # both skipped, so a mode documented as "no re-tracking" would silently
        # fall through to a ~30-minute local CPU re-track.
        tracker = _track_ball_cached(local, tmp, sample_every=sample_every, r2_key=r2_key)
        contacts = find_contacts(tracker, frame_height=frame_h)
        detections = contacts_to_rallies(contacts, duration, frame_h)
        if audio is not None:
            detections = score_cheers(detections, *audio)
        detections = score_highlights(str(local), detections, use_clip=settings.clip_verify_enabled)

        threshold = settings.highlight_score_threshold
        pre = [
            ModelWindow(float(d["start"]), float(d["end"]), d.get("highlight_score"))
            for d in detections
        ]
        post = [w for w in pre if (w.score or 0.0) >= threshold]
        print(
            f"Offline: {len(detections)} rallies scored; "
            f"{len(post)} above gate ({threshold:.2f})"
        )
        return pre, post


# ── dead-time mode (CF-98) ─────────────────────────────────────────────────

@dataclass
class DeadFixture:
    test_id: str
    keep: list[Interval]        # human-labeled rally / in-play spans
    duration: float
    raw: dict


def load_deadtime_fixture(test_id: str) -> DeadFixture:
    """
    Load a dead-time fixture: the in-play spans; dead time is the complement.

    The span list may carry a `tier` per span (the labeling pass tags every
    labeled span, in-play or not). `keep_tiers` selects which tiers count as
    ball-in-play — everything else falls through to dead time via the
    complement. Mirrors load_fixture()'s ground_truth_tiers filter, and is
    equally permissive: absent key, or a span with no tier, counts as in-play.
    That distinction is load-bearing here — a non-highlight rally (failed serve,
    "average play") is still live ball the condense stage must keep, while a
    BREAK or a camera outlier is genuinely dead.

    `spans` is the current key; `keep` is accepted as the original name from
    fixtures written before tiers existed (they list in-play spans only).
    """
    path = FIXTURES_DIR / f"{test_id}_deadtime.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    tiers = data.get("keep_tiers")
    spans = data.get("spans", data.get("keep", []))
    keep: list[Interval] = [
        (parse_timestamp(s["start"]), parse_timestamp(s["end"]))
        for s in spans
        if tiers is None or s.get("tier") is None or s["tier"] in tiers
    ]
    duration = data.get("video_duration_sec")
    if duration is None:  # permissive: fall back to the last labeled rally end
        duration = max((e for _, e in keep), default=0.0)
    return DeadFixture(test_id=data.get("test_id", test_id), keep=keep, duration=float(duration), raw=data)


def load_windows_json(path: Path) -> list[Interval]:
    """Load model keep-windows from {"keep": [{start, end}, ...]} (or a bare list)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("keep") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit(
            f'{path}: expected {{"keep": [{{"start", "end"}}, ...]}} or a bare list of spans'
        )
    return [(parse_timestamp(w["start"]), parse_timestamp(w["end"])) for w in items]


def _run_offline_deadtime(test_id: str) -> tuple[list[Interval], list[Interval], str, str]:
    """
    Derive the condense keep-windows from the fixture's source video, mirroring
    process_game_task's stage-5 condense path (dead_time.py). Ball positions load
    from the R2 cache (free unless model/sample-rate changed), so no re-tracking.

    Follows `condense_mode`, so this scores the builder production actually runs
    rather than a fixed one — the whole point of the offline path is that its
    numbers transfer.

    Returns (companion, shipped, comparison_title, companion_tag). `shipped` is
    what the condense stage would produce under the current mode; `companion` is
    the one-step-back configuration it is worth attributing the difference to —
    pre-bridge windows under "rules", the whole rule-based path under the modes
    that replace it.

    Runs impure edges (R2, ffmpeg, cv2, app config) behind lazy imports — must be
    invoked where those deps live — the `eval` service, not `worker`,
    which carries production's resource limits (CF-241).
    """
    import tempfile

    import cv2

    from app.config import settings
    from app.services import storage as s3
    from app.workers.tasks import _track_ball_cached
    from ml.pipeline.ball import find_contacts
    from ml.pipeline.dead_time import (
        Abstained,
        active_windows_from_contacts,
        active_windows_guarded,
        bridge_windows_by_motion,
    )

    fixture = load_deadtime_fixture(test_id)
    r2_key = _require_r2_key(fixture.raw, test_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        local = tmp / "game.mp4"
        print(f"Downloading {r2_key} from R2...")
        s3.download_file(r2_key, local)

        cap = cv2.VideoCapture(str(local))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        # A container OpenCV cannot decode reports 0 here, and every guarded
        # speed threshold is normalized by frame height — so the builder would
        # raise ValueError several frames of stack away from the cause. This
        # function's other bad-input paths all exit with instructions; so does
        # this one.
        if frame_h <= 0:
            raise SystemExit(
                f"Offline deadtime: OpenCV read a frame height of {frame_h} from "
                f"{r2_key}. Its codec is probably unavailable in this environment "
                "— check the file plays locally, or score a dumped keep-window "
                "list with --windows-json instead."
            )
        # Prefer the fixture's declared duration (the same value that anchors the
        # dead-time complement); fall back to the decoded frame count.
        duration = fixture.duration or (n_frames / fps)

        _assert_declared_frame_height(fixture, frame_h, test_id, r2_key, "deadtime")

        sample_every = max(1, round(fps / 3.0))  # matches process_game_task
        # Pass r2_key so the ball-cache lookup hits; without it this silently
        # falls through to a ~30-minute local CPU re-track (see _run_offline).
        tracker = _track_ball_cached(local, tmp, sample_every=sample_every, r2_key=r2_key)
        contacts = find_contacts(tracker, frame_height=frame_h)

        pre_bridge = active_windows_from_contacts(
            contacts, duration,
            gap_seconds=settings.condense_gap_seconds,
            pad_before=settings.condense_pad_before,
            pad_after=settings.condense_pad_after,
            min_contacts=settings.condense_min_contacts,
            merge_gap_seconds=settings.condense_merge_gap_seconds,
        )
        # Mirrors what process_game_task keeps for the condense stage.
        positions = [
            {"time": p.time, "x": p.x, "y": p.y} for p in tracker.positions
        ]

        mode = settings.condense_mode
        # Offline mode is the ball-cache path by design ("no re-tracking"). No
        # ball signal at all means the cache is empty; the production fallback
        # is a ~30-min pose-first CPU re-detect, which this mode deliberately
        # does not run. Fail loudly rather than score that as the model's output.
        #
        # Zero *contacts* is no longer that case for the guarded builder: CF-187
        # gave it motion anchors precisely so a rally the detector never saw
        # still opens a window (19 of test4's 46 rallies produce no contact),
        # so contact-free input with a real track is a legitimate thing to
        # score — and refusing it here would decline exactly the case the
        # builder was added for. It still needs a track: with no positions
        # either, there is nothing for anchors to work from.
        if not contacts and not (mode == "guarded" and positions):
            raise SystemExit(
                f"Offline deadtime: ball cache for {r2_key} yielded no contacts"
                f"{' and no positions' if mode == 'guarded' else ''}. "
                "Score the pose-first fallback via --windows-json with a dumped "
                "keep-window list instead."
            )
        post_bridge = bridge_windows_by_motion(
            pre_bridge, positions,
            speed_pxps=settings.condense_bridge_speed_pxps,
            fast_fraction=settings.condense_bridge_fast_fraction,
            max_bridge_seconds=settings.condense_bridge_max_seconds,
            frame_height=frame_h,
        )

        if mode == "rules":
            print(
                f"Offline deadtime [mode=rules]: {len(contacts)} contacts → "
                f"{len(pre_bridge)} windows → {len(post_bridge)} after motion bridge"
            )
            return pre_bridge, post_bridge, "MOTION BRIDGE (CF-46)", "nobridge"

        if mode == "guarded":
            shipped = active_windows_guarded(
                contacts, positions, duration, frame_h,
                gap_seconds=settings.condense_gap_seconds,
                min_contacts=settings.condense_min_contacts,
                pad_before=settings.condense_guard_pad_before,
                pad_after=settings.condense_guard_pad_after,
                merge_gap_seconds=settings.condense_guard_merge_gap_seconds,
                gate_speed=settings.condense_guard_gate_speed,
                anchor_speed=settings.condense_guard_anchor_speed,
                min_track_rate=settings.condense_guard_min_track_rate,
            )
            title = "GUARDED vs RULES (CF-187)"
        else:
            # Unreachable via settings since condense_mode became a Literal (a
            # typo now fails at Settings construction). Kept because this reads
            # settings directly, so it is the one guard left if that annotation
            # is ever widened back to str — unlike tasks._build_condense_windows'
            # twin, which also still serves direct callers passing a mode string.
            raise SystemExit(f"Unknown condense_mode {mode!r} — expected rules or guarded")

        # The abstain (one whole-video window) is a real outcome worth seeing in
        # the report, not an error — say so, because a 0% dead-removed row
        # otherwise reads as a broken run. Asked of the builder's own result
        # rather than matched on shape: a genuine condense that happens to keep
        # everything is the same list and a different decision.
        if isinstance(shipped, Abstained):
            print(f"Offline deadtime [mode={mode}]: ABSTAINED — ball track too sparse to condense")
        else:
            print(
                f"Offline deadtime [mode={mode}]: {len(contacts)} contacts → "
                f"{len(shipped)} windows (rule-based path: {len(post_bridge)})"
            )
        return post_bridge, shipped, title, "rules"


def format_deadtime(s: DeadTimeSignals) -> str:
    return "\n".join([
        "== DEAD-TIME / CONDENSE ==",
        f"  Dead-time removed:    {_fmt_pct(s.dead_removed_pct)}   "
        f"(of {s.human_dead_sec:.0f}s dead)   [aggressiveness]",
        f"  Live wrongly removed: {s.live_removed_sec:.0f}s = {_fmt_pct(s.live_removed_pct)} "
        f"of {s.human_keep_sec:.0f}s play   [the harm]",
        f"  Kept-play (recall):   {_fmt_pct(s.kept_play_pct)}",
        f"  Condense ratio:       {_fmt_pct(s.condense_ratio)}   "
        f"({s.model_keep_sec:.0f}s kept / {s.duration:.0f}s)",
    ])


DEFAULT_AUDIT_LIMIT = 12


def format_deadtime_audit(
    s: DeadTimeSignals, *, limit: int = DEFAULT_AUDIT_LIMIT, recorded: bool = True
) -> str:
    """
    Render the two divergence lists, longest span first.

    A real run produces hundreds of spans; printing all of them buries the few
    that matter. Since the lists are sorted longest-first and the long spans
    hold most of the seconds, showing the worst `limit` and summarizing the rest
    loses nothing actionable. The complete lists always go to the results log,
    so nothing is lost for later analysis. limit <= 0 prints everything.
    """
    def block(spans: list[Interval], header: str) -> list[str]:
        if not spans:
            return [f"  {header}: none"]
        total = sum(b - a for a, b in spans)
        rows = [f"  {header}: {len(spans)} spans, {total:.0f}s total"]
        shown = spans if limit <= 0 else spans[:limit]
        rows += [f"    {_seconds_to_ts(a)}-{_seconds_to_ts(b)}  {b - a:5.0f}s" for a, b in shown]
        hidden = spans[len(shown):]
        if hidden:
            # Under --no-record there is no log to point at, so don't send the
            # reader looking for one.
            where = (
                f" (all {len(spans)} in the results log)" if recorded
                else f" (all {len(spans)} shown with --audit-limit 0)"
            )
            rows.append(
                f"    + {len(hidden)} shorter, {sum(b - a for a, b in hidden):.0f}s total"
                f"{where}"
            )
        return rows

    lines = block(s.over_cut_live, "OVER-CUT LIVE (real play removed - act on these)")
    lines.append("")
    lines += block(s.missed_dead, "MISSED DEAD (dead time kept)")
    return "\n".join(lines)


def format_deadtime_comparison(
    title: str, left_label: str, left: DeadTimeSignals, right_label: str, right: DeadTimeSignals,
) -> str:
    """
    Compact side-by-side of two configurations' headline numbers.

    Beats re-printing a whole second report: when comparing two runs over the
    same fixture the only question is what moved, and a full duplicate report
    forces the reader to diff hundreds of lines by eye.
    """
    def pct_row(label: str, a: float | None, b: float | None) -> str:
        delta = "n/a" if a is None or b is None else f"{(b - a) * 100:+.1f}pp"
        return f"  {label:<24}{_fmt_pct(a):>10}{_fmt_pct(b):>12}{delta:>10}"

    live_delta = right.live_removed_sec - left.live_removed_sec
    return "\n".join([
        f"== {title} ==",
        f"  {'':<24}{left_label:>10}{right_label:>12}{'delta':>10}",
        pct_row("Dead-time removed:", left.dead_removed_pct, right.dead_removed_pct),
        f"  {'Live wrongly removed:':<24}{left.live_removed_sec:>9.0f}s"
        f"{right.live_removed_sec:>11.0f}s{live_delta:>+9.0f}s",
        pct_row("Kept-play (recall):", left.kept_play_pct, right.kept_play_pct),
        pct_row("Condense ratio:", left.condense_ratio, right.condense_ratio),
    ])


def _deadtime_to_dict(s: DeadTimeSignals) -> dict:
    return {
        "dead_removed_pct": _round(s.dead_removed_pct),
        "live_removed_sec": _round(s.live_removed_sec),
        "live_removed_pct": _round(s.live_removed_pct),
        "kept_play_pct": _round(s.kept_play_pct),
        "condense_ratio": _round(s.condense_ratio),
        # These two partition [0, duration] by construction in
        # evaluate_deadtime, so they sum to `duration` exactly before rounding
        # and can fail to afterwards — the same shape CF-352 fixed for
        # incorrect_seconds. Measured: human=(0, 100.00005) over a 200.0001s
        # duration writes 100.0001 + 100.0001 = 200.0002 beside a duration of
        # 200.0001. Deliberately not changed here: unlike incorrect_seconds,
        # where `total` is derived from the parts, all three of these are
        # independent fields and deciding which one gives way is a call about
        # what the file means, not a rounding detail. Measured and argued in
        # #401; `kept_play_pct` / `live_removed_pct` were checked and are *not*
        # exposed — 800k samples plus every half-ulp tie point at the 5th
        # decimal produced no case where the rounded pair fails to sum to 1.0.
        "human_keep_sec": _round(s.human_keep_sec),
        "human_dead_sec": _round(s.human_dead_sec),
        "model_keep_sec": _round(s.model_keep_sec),
        "duration": _round(s.duration),
        "over_cut_live": [[round(a, 3), round(b, 3)] for a, b in s.over_cut_live],
        "missed_dead": [[round(a, 3), round(b, 3)] for a, b in s.missed_dead],
    }


def append_deadtime_result(test_id: str, version: str, s: DeadTimeSignals) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version_tag": version,
        "git_commit": _git_commit(),
        "config_snapshot": config_snapshot(),
        "deadtime": _deadtime_to_dict(s),
    }
    path = RESULTS_DIR / f"{test_id}_deadtime.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return path


def run_deadtime(
    test_id: str,
    version: str,
    model_keep: list[Interval],
    *,
    record: bool = True,
    audit_limit: int = DEFAULT_AUDIT_LIMIT,
    report: bool = True,
) -> DeadTimeSignals:
    """Score model_keep against the fixture. report=False computes silently."""
    fx = load_deadtime_fixture(test_id)
    s = evaluate_deadtime(fx.keep, model_keep, fx.duration)
    if report:
        print(f"\nDead-time eval - test={test_id}  version={version}")
        print(f"Ground truth: {len(fx.keep)} rally regions over {fx.duration:.0f}s\n")
        print(format_deadtime(s))
        print()
        print(format_deadtime_audit(s, limit=audit_limit, recorded=record))
    if record:
        path = append_deadtime_result(test_id, version, s)
        print(f"\nAppended result -> {path}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a model run against a ground-truth fixture.")
    ap.add_argument("--test", required=True, help="fixture id, e.g. test1")
    ap.add_argument("--version", required=True, help="free-text version tag for this run")
    ap.add_argument("--mode", choices=["highlight", "deadtime"], default="highlight",
                    help="highlight = clip quality (CF-55); deadtime = condense quality (CF-98)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--clips-json", type=Path, help="highlight: dumped {pre_gate, post_gate} windows")
    src.add_argument("--windows-json", type=Path, help="deadtime: dumped {keep: [{start, end}]} keep-windows")
    src.add_argument("--offline", action="store_true", help="re-run pipeline stages via the R2 ball-cache")
    ap.add_argument("--no-record", action="store_true", help="print only; don't append to results log")
    ap.add_argument("--audit-limit", type=int, default=DEFAULT_AUDIT_LIMIT,
                    help=f"deadtime: max spans per audit list (default {DEFAULT_AUDIT_LIMIT}; 0 = all). "
                         "The full lists go to the results log unless --no-record.")
    ap.add_argument("--dump-windows", type=Path,
                    help="deadtime --offline: also write the derived keep-windows to this JSON, "
                         "so later runs can re-score them anywhere via --windows-json "
                         "(no Docker, no video download)")
    args = ap.parse_args()

    if args.dump_windows and not (args.mode == "deadtime" and args.offline):
        ap.error("--dump-windows only applies to --mode deadtime --offline")

    if args.mode == "deadtime":
        if args.windows_json:
            model_keep = load_windows_json(args.windows_json)
            run_deadtime(
                args.test, args.version, model_keep,
                record=not args.no_record, audit_limit=args.audit_limit,
            )
        elif args.offline:
            companion, shipped, title, tag = _run_offline_deadtime(args.test)
            if args.dump_windows:
                # keep = shipping windows, the ones --windows-json reads back.
                # The companion rides along under a key the loader ignores, so
                # the comparison can be reconstructed from the dump too.
                spans_of = lambda ws: [  # noqa: E731
                    {"start": round(a, 3), "end": round(b, 3)} for a, b in ws
                ]
                args.dump_windows.write_text(
                    json.dumps(
                        {"keep": spans_of(shipped), f"keep_{tag}": spans_of(companion)},
                        indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )
                print(f"Dumped keep-windows -> {args.dump_windows}")
            # Report + record the shipping windows — that's the configuration
            # under test. Then score the companion silently and show only what
            # the mode moved: a second full report would double an already long
            # audit to say very little.
            post = run_deadtime(
                args.test, args.version, shipped,
                record=not args.no_record, audit_limit=args.audit_limit,
            )
            if companion != shipped:
                pre = run_deadtime(
                    args.test, f"{args.version}-{tag}", companion,
                    record=False, report=False,
                )
                print()
                print(format_deadtime_comparison(
                    f"{title} - not recorded",
                    tag, pre, "shipping", post,
                ))
            else:
                print(f"\n({title} changed nothing this run)")
        else:
            ap.error("--mode deadtime needs --windows-json or --offline")
        return

    if args.windows_json:
        ap.error("--windows-json is for --mode deadtime; highlight mode uses --clips-json")
    if args.offline:
        pre_windows, post_windows = _run_offline(args.test)
    else:
        pre_windows, post_windows = load_clips_json(args.clips_json)

    run(args.test, args.version, pre_windows, post_windows, record=not args.no_record)


if __name__ == "__main__":
    main()
