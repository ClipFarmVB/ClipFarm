"""
Evaluation harness (CF-55): score a model run against a ground-truth fixture
and append a tagged row to the results log so improvement is tracked across
versions.

metrics.py holds the pure math; this module does the impure edges — loading
fixtures, acquiring the model's windows, printing the report, and writing
results. Any DB/R2/pipeline access stays behind lazy imports so `import
ml.eval.metrics` never drags in the app.

Model-window acquisition modes:
  --clips-json PATH   read pre-gate and post-gate windows from a dumped JSON
                      {"pre_gate": [{start,end,highlight_score}], "post_gate": [...]}
  --offline           (coming next) re-run the pipeline stages against the
                      fixture's source video via the R2 ball-cache

Usage:
  python -m ml.eval.harness --test test1 --version <label> --clips-json dump.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ml.eval.metrics import EvalSignals, Interval, ModelWindow, evaluate

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
    clips: list[Interval] = [
        (parse_timestamp(c["start"]), parse_timestamp(c["end"])) for c in data["clips"]
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
        f"  Captured %:     {_fmt_pct(s.captured_pct)}   ({s.human_seconds:.0f}s labeled, {s.model_seconds:.0f}s clipped)",
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
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


# ── results log ────────────────────────────────────────────────────────────

def _git_commit() -> str | None:
    # Env override lets an in-container run (no .git mounted) record the host commit.
    env = __import__("os").environ.get("GIT_COMMIT")
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
    snap: dict[str, dict[str, float]] = {}
    for name in _SNAPSHOT_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        snap[name] = {
            attr: getattr(mod, attr)
            for attr in dir(mod)
            if attr.isupper() and isinstance(getattr(mod, attr), (int, float)) and not isinstance(getattr(mod, attr), bool)
        }
    return snap


def _signals_to_dict(s: EvalSignals) -> dict:
    return {
        "captured_pct": s.captured_pct,
        "buckets": {
            "well_captured": s.buckets.well_captured,
            "butchered": s.buckets.butchered,
            "missed": s.buckets.missed,
            "total": s.buckets.total,
        },
        "incorrect_seconds": {
            "junk": s.incorrect.junk,
            "lead_slop": s.incorrect.lead_slop,
            "tail_slop": s.incorrect.tail_slop,
            "bridge": s.incorrect.bridge,
            "total": s.incorrect.total,
        },
        "auc": s.auc,
        "human_seconds": s.human_seconds,
        "model_seconds": s.model_seconds,
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


def _run_offline(test_id: str) -> tuple[list[ModelWindow], list[ModelWindow]]:
    """
    Re-run the detection + scoring stages against the fixture's source video,
    mirroring process_game_task stages 0-2. Ball positions load from the R2
    cache (free unless model/sample-rate changed), so no re-tracking. Returns
    (pre_gate = all scored rallies, post_gate = those above the gate).

    Runs impure edges (R2, ffmpeg, cv2, app config) behind lazy imports — must
    be invoked where those deps live (the worker container).
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
    r2_key = fixture.raw["source_r2_key"]

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

        audio = compute_audio_energy(str(local))

        sample_every = max(1, round(fps / 3.0))  # matches process_game_task
        tracker = _track_ball_cached(local, tmp, sample_every=sample_every)
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a model run against a ground-truth fixture.")
    ap.add_argument("--test", required=True, help="fixture id, e.g. test1")
    ap.add_argument("--version", required=True, help="free-text version tag for this run")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--clips-json", type=Path, help="dumped {pre_gate, post_gate} windows")
    src.add_argument("--offline", action="store_true", help="re-run pipeline stages via the R2 ball-cache")
    ap.add_argument("--no-record", action="store_true", help="print only; don't append to results log")
    args = ap.parse_args()

    if args.offline:
        pre_windows, post_windows = _run_offline(args.test)
    else:
        pre_windows, post_windows = load_clips_json(args.clips_json)

    run(args.test, args.version, pre_windows, post_windows, record=not args.no_record)


if __name__ == "__main__":
    main()
