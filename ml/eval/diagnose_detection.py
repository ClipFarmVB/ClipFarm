"""
Detection-recall diagnostic (CF-98 follow-up).

The CF-98 baseline showed the condense stage deleting 41.6% of real play, and
traced the cause upstream: 214 ball contacts over 126 labeled rallies, so whole
rallies are never detected. This tool answers the next question — *which half*
of the detection chain loses them:

    video → ball model (sampled frames) → positions → find_contacts() → contacts

For every labeled rally it counts the ball positions and the contacts falling
inside, which separates two failures with completely different fixes:

  BLIND    positions == 0  — the model never saw the ball here. Fixing this
                             means sample rate, model, or weights.
  REJECTED positions >= 1 but contacts == 0 — the ball *was* tracked, and
                             find_contacts() declined to call any of it a hit.
                             Fixing this is threshold work, no re-tracking.

It also dumps the raw positions and contacts to JSON so the rejection case can
be iterated on locally at zero cost (no container, no video download), the same
way --dump-windows lets the harness re-score offline runs.

Run it where the worker deps live:

  docker compose run --rm --no-deps worker \
    python -m ml.eval.diagnose_detection --test test1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.eval.harness import RESULTS_DIR, _seconds_to_ts, load_deadtime_fixture
from ml.pipeline.intervals import Interval



def _count_in(spans: list[Interval], times: list[float]) -> list[int]:
    """
    How many of `times` fall inside each span. Linear sweep — `times` is sorted
    and the fixture guarantees spans are sorted and non-overlapping, so the
    cursor only ever moves forward.
    """
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--test", default="test1", help="fixture id (default test1)")
    ap.add_argument("--dump", type=Path, default=None,
                    help="write positions+contacts JSON here "
                         "(default results/{test}_ball_track.json)")
    args = ap.parse_args()

    import tempfile

    import cv2

    from app.services import storage as s3
    from app.workers.tasks import _track_ball_cached
    from ml.pipeline.ball import find_contacts

    fx = load_deadtime_fixture(args.test)
    r2_key = fx.raw["source_r2_key"]
    rallies = sorted(fx.keep)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        local = tmp / "game.mp4"
        print(f"Downloading {r2_key} from R2...")
        s3.download_file(r2_key, local)

        cap = cv2.VideoCapture(str(local))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        sample_every = max(1, round(fps / 3.0))  # matches process_game_task
        tracker = _track_ball_cached(local, tmp, sample_every=sample_every, r2_key=r2_key)
        contacts = find_contacts(tracker, frame_height=frame_h)

    pos_times = sorted(p.time for p in tracker.positions)
    con_times = sorted(float(c["time"]) for c in contacts)

    # Effective sampling: at fps/3 the model looks every ~1/3 s. A contact is a
    # direction change, so touches between two looks are invisible by construction.
    effective_fps = fps / sample_every
    print(f"\nVideo {fps:.1f} fps, sampling every {sample_every} frames "
          f"= {effective_fps:.1f} looks/sec (one every {1 / effective_fps:.2f}s)")
    print(f"Ball positions: {len(pos_times)}   contacts: {len(con_times)}")

    pos_in = _count_in(rallies, pos_times)
    con_in = _count_in(rallies, con_times)

    blind = [i for i, n in enumerate(pos_in) if n == 0]
    rejected = [i for i in range(len(rallies)) if pos_in[i] > 0 and con_in[i] == 0]
    detected = [i for i, n in enumerate(con_in) if n > 0]

    rally_sec = sum(b - a for a, b in rallies)
    in_rally_pos = sum(pos_in)
    in_rally_con = sum(con_in)

    # Denominator interpolated, not hardcoded: --test selects the fixture, so
    # this table is read against whatever rally count that fixture has.
    print(f"\n{'':<12}{'rallies':>9}{f'% of {len(rallies)}':>10}   what it means")
    print(f"  {'BLIND':<10}{len(blind):>9}{100 * len(blind) / len(rallies):>9.1f}%"
          "   no ball positions at all — model never saw it")
    print(f"  {'REJECTED':<10}{len(rejected):>9}{100 * len(rejected) / len(rallies):>9.1f}%"
          "   tracked, but find_contacts() called nothing a hit")
    print(f"  {'DETECTED':<10}{len(detected):>9}{100 * len(detected) / len(rallies):>9.1f}%"
          "   >= 1 contact")

    print(f"\nPositions inside rallies: {in_rally_pos} of {len(pos_times)} "
          f"({100 * in_rally_pos / max(len(pos_times), 1):.1f}%), "
          f"while rallies are {100 * rally_sec / fx.duration:.1f}% of the video")
    print(f"Contacts inside rallies:  {in_rally_con} of {len(con_times)} "
          f"({100 * in_rally_con / max(len(con_times), 1):.1f}%)")
    if detected:
        print(f"Contacts per detected rally: {in_rally_con / len(detected):.1f} "
              "(a real rally has 4+ touches per side)")

    print("\nWorst rallies (most positions, still zero contacts):")
    worst = sorted(rejected, key=lambda i: -pos_in[i])[:10]
    if not worst:
        print("  none — every tracked rally produced at least one contact")
    for i in worst:
        a, b = rallies[i]
        print(f"    {_seconds_to_ts(a)}-{_seconds_to_ts(b)}  {b - a:4.0f}s  "
              f"{pos_in[i]:3d} positions, 0 contacts")

    dump = args.dump or (RESULTS_DIR / f"{args.test}_ball_track.json")
    # parents=True: an explicit --dump into a nested path would otherwise raise
    # here, after the R2 download and the tracking pass have already been paid for.
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_text(json.dumps({
        "test_id": args.test,
        "source_r2_key": r2_key,
        "fps": fps,
        "sample_every": sample_every,
        "frame_height": frame_h,
        "positions": [{"time": p.time, "x": p.x, "y": p.y} for p in tracker.positions],
        "contacts": [{"time": float(c["time"])} for c in contacts],
    }, indent=1) + "\n", encoding="utf-8")
    print(f"\nDumped track -> {dump}  (iterate on find_contacts locally from this)")


if __name__ == "__main__":
    main()
