"""
Confidence sweep for the ball detector (CF-171 step 1).

Answers the question that decides how much work CF-171 needs: on non-Mikasa
footage, is the detector BLIND to the ball, or does it see it and get gated out
by MIN_CONF = 0.40?

  BLIND             nothing at any threshold — the model has no shape prior for
                    this ball and only new training data fixes it.
  UNDER-THRESHOLD   detections exist at 0.15-0.35 — the model does find the ball
                    but is unsure, which is what colour augmentation fixes.

diagnose_detection.py cannot answer this: MIN_CONF is baked into the
`model.infer()` call in ball.py, and the R2 ball cache is keyed on
(md5, model, sample_every) with no confidence term, so a sweep run there
silently returns the same 0.40 positions at every threshold.

So this tool splits the two halves that diagnose_detection fuses:

  pass A (expensive, once)  decode + infer at --floor, keep EVERY raw detection
  pass B (cheap, repeated)  replay track_ball's linking at each threshold

Pass A dominates the cost (~2k inferences for a 10-minute video), and its
output is a superset of every threshold above the floor — so the sweep is one
inference pass, not one per threshold. Pass B re-implements nothing: it drives
the same _pick_active + max_jump/max_age rules track_ball uses, so a row at
0.40 reproduces the production track.

Raw detections cache to --cache-dir as {md5}-raw{floor}.json, so re-running the
sweep (or scoring a new threshold) is free after the first pass.

Runs against a LOCAL video file — no R2, no db. Needs the `inference` package,
so run it where the worker deps live:

  docker compose run --rm --no-deps \
    -v "$PWD/DeadtimeLabel3.mp4:/app/DeadtimeLabel3.mp4" worker \
    python -m ml.eval.sweep_ball_conf --test test3 --video DeadtimeLabel3.mp4
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ml.eval.harness import load_deadtime_fixture

DEFAULT_THRESHOLDS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


def _file_md5(path: Path) -> str:
    """Mirrors tasks.py._file_md5 so the cache name matches the fixture's md5."""
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_in(spans: list[tuple[float, float]], times: list[float]) -> list[int]:
    """How many of `times` fall inside each span (both sorted; cursor only advances)."""
    counts = [0] * len(spans)
    i = 0
    for idx, (start, end) in enumerate(spans):
        while i < len(times) and times[i] < start:
            i += 1
        j = i
        while j < len(times) and times[j] <= end:
            j += 1
        counts[idx] = j - i
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Pass A — one inference sweep at the floor, every detection kept
# ─────────────────────────────────────────────────────────────────────────────

def collect_raw(video: Path, floor: float, cache_dir: Path, model_id: str) -> dict:
    """
    Infer on every sample_every-th frame at `floor` confidence and keep all
    predictions, not just the picked one. Cached by (md5, model, floor).

    The model id is part of the cache key for the same reason it is part of the
    R2 ball-cache key: a retrained detector returns different boxes for the same
    frames, so without it a sweep against a new version silently replays the
    old one's detections and reports them as the new model's.
    """
    md5 = _file_md5(video)
    model_slug = model_id.replace("/", "-")
    cache = cache_dir / f"{md5}-{model_slug}-raw{floor:g}.json"
    if cache.exists():
        print(f"Raw-detection cache hit: {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))

    import os

    import cv2

    from inference import get_model

    api_key = os.environ.get("ROBOFLOW_API_KEY", "")
    if not api_key:
        raise SystemExit("ROBOFLOW_API_KEY is not set")

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sample_every = max(1, round(fps / 3.0))  # matches process_game_task

    model = get_model(model_id, api_key=api_key)
    print(f"{video.name} [{model_id}]: {total} frames @ {fps:.1f} fps, sampling every "
          f"{sample_every} -> ~{total // sample_every} inferences at conf >= {floor}")

    # grab() past skipped frames instead of read() — same CF-42 decode saving
    # track_ball takes, and it is most of the wall clock at sample_every=10.
    frames: list[dict] = []
    frame_idx = 0
    while True:
        if frame_idx % sample_every == 0:
            ret, frame = cap.read()
            if not ret:
                break
            results = model.infer(frame, confidence=floor)
            preds = []
            if results and hasattr(results[0], "predictions"):
                for p in results[0].predictions:
                    preds.append({"x": float(p.x), "y": float(p.y),
                                  "confidence": float(p.confidence)})
            preds.sort(key=lambda d: d["confidence"], reverse=True)
            frames.append({"frame": frame_idx, "time": frame_idx / fps, "preds": preds})
            if len(frames) % 200 == 0:
                print(f"  {frame_idx}/{total} frames, "
                      f"{sum(len(f['preds']) for f in frames)} detections so far")
        else:
            if not cap.grab():
                break
        frame_idx += 1
    cap.release()

    data = {
        "video": video.name,
        "md5": md5,
        "model": model_id,
        "fps": fps,
        "frame_height": frame_h,
        "sample_every": sample_every,
        "conf_floor": floor,
        "frames": frames,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data) + "\n", encoding="utf-8")
    print(f"Wrote raw detections -> {cache}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Pass B — replay track_ball's linking at a threshold, offline
# ─────────────────────────────────────────────────────────────────────────────

def track_at(raw: dict, threshold: float):
    """
    Rebuild the track from cached detections as track_ball would at
    MIN_CONF = threshold. Same _pick_active, same jump/age scaling — only the
    inference call and the decode are replaced by the cache.
    """
    from ml.pipeline.ball import (
        MAX_JUMP_PX,
        MAX_MISS,
        SAMPLE_EVERY,
        BallPosition,
        TrackedBall,
        _pick_active,
    )

    sample_every = raw["sample_every"]
    max_jump = MAX_JUMP_PX * (sample_every / SAMPLE_EVERY)
    max_age_frames = MAX_MISS * sample_every

    tracker = TrackedBall()
    for f in raw["frames"]:
        dets = [d for d in f["preds"] if d["confidence"] >= threshold]
        active = _pick_active(dets, tracker, f["frame"],
                              max_jump=max_jump, max_age_frames=max_age_frames)
        if active:
            tracker.misses = 0
            tracker.positions.append(BallPosition(
                frame=f["frame"], time=f["time"],
                x=active["x"], y=active["y"], confidence=active["confidence"],
            ))
        else:
            tracker.misses += 1
    return tracker


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--test", default="test3", help="dead-time fixture id (default test3)")
    ap.add_argument("--video", type=Path, required=True, help="local video file")
    ap.add_argument("--floor", type=float, default=0.05,
                    help="inference confidence floor for pass A (default 0.05)")
    ap.add_argument("--cache-dir", type=Path, default=Path("ml/eval/ball_caches"))
    ap.add_argument("--thresholds", type=float, nargs="+", default=DEFAULT_THRESHOLDS)
    ap.add_argument("--model", default=None,
                    help="Roboflow model id (default: ball.py's MODEL_ID)")
    args = ap.parse_args()

    from ml.pipeline.ball import MODEL_ID, find_contacts

    model_id = args.model or MODEL_ID

    fx = load_deadtime_fixture(args.test)
    rallies = sorted(fx.keep)
    rally_sec = sum(b - a for a, b in rallies)

    raw = collect_raw(args.video, args.floor, args.cache_dir, model_id)
    frame_h = raw["frame_height"]

    n_frames = len(raw["frames"])
    n_dets = sum(len(f["preds"]) for f in raw["frames"])
    seen = sum(1 for f in raw["frames"] if f["preds"])
    print(f"\nModel: {model_id}")
    print(f"\nPass A: {n_frames} sampled frames, {n_dets} raw detections at "
          f"conf >= {args.floor}; {seen} frames ({100 * seen / max(n_frames, 1):.1f}%) "
          "have at least one")

    play_frac = rally_sec / fx.duration
    print(f"\n{args.test}: {len(rallies)} labeled rallies, {rally_sec:.0f}s of play "
          f"in {fx.duration:.0f}s of video ({100 * play_frac:.1f}%)")
    print(f"\n{'conf':>6}{'positions':>11}{'pos/s play':>12}{'LIFT':>7}{'BLIND':>7}"
          f"{'DETECTED':>10}{'contacts':>10}   (of {len(rallies)} rallies)")
    for t in sorted(args.thresholds):
        tracker = track_at(raw, t)
        contacts = find_contacts(tracker, frame_height=frame_h)
        pos_times = sorted(p.time for p in tracker.positions)
        con_times = sorted(float(c["time"]) for c in contacts)
        pos_in = _count_in(rallies, pos_times)
        con_in = _count_in(rallies, con_times)
        blind = sum(1 for n in pos_in if n == 0)
        detected = sum(1 for n in con_in if n > 0)
        # LIFT is the honest signal — see the note below. Position counts alone
        # cannot tell recovered ball from recovered noise; this can.
        lift = (sum(pos_in) / max(len(pos_times), 1)) / play_frac
        print(f"{t:>6.2f}{len(pos_times):>11}{sum(pos_in) / rally_sec:>12.2f}"
              f"{lift:>7.2f}{blind:>7}{detected:>10}{len(con_times):>10}")

    print(f"""
LIFT is the column that answers CF-171. It is the share of tracked positions
falling inside a labeled rally, divided by the share of the video that is play
({100 * play_frac:.1f}%). The ball only exists during rallies; noise does not care.

  LIFT ~ 1.0   positions are spread uniformly over the video — the tracker is
               following noise, whatever the position count says
  LIFT > 1.3   positions concentrate in play — this is really the ball

Read LIFT *before* BLIND and DETECTED, because both of those improve when the
threshold drops even if every added detection is noise: a rally stops being
BLIND the moment one spurious box lands inside it. A threshold that lowers
BLIND while LIFT falls toward 1.0 has recovered nothing.

So the decision this tool exists to make:

  LIFT climbs as the threshold falls    ball is seen but under-confident;
                                        colour augmentation should close it
  LIFT stays flat near 1.0              the sub-threshold detections are not
                                        the ball; augmentation alone will not
                                        be enough and new data is needed""")


if __name__ == "__main__":
    main()
