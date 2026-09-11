"""
Is the tracker following the ball, or a chair? (CF-229, #233)

CF-174 normalized the CONTACT px/s thresholds by frame height and could not do
the same for the SEGMENTATION ones. `ml/pipeline/ball.py` records why, and the
reason is not a property of those constants:

  * `SEG_MAX_SPEED_PXPS` *should* scale by the same argument, and leaving it
    fixed splits 12.2% of test4's samples and 4.4% of test2's as bogus track
    hops at 1080p. Raising it merges test4's STATIONARY FALSE-POSITIVE
    detections into the ball's own segments, whose median speed then falls
    under the held/spare-ball filter, and the fixture collapses to zero
    contacts.
  * `SEG_MIN_MEDIAN_SPEED_PXPS` cannot scale either, for the same collapse.

So both constants are pinned by one upstream defect — **roughly half of
test4's samples sit at ~0 px/s, because the detector is locked onto something
that is not the ball.** CF-229's acceptance is a number about that: "test4's
~0 px/s sample fraction materially below 50%".

Nobody can produce that number today without writing this script, which is why
it exists. It is an INSTRUMENT, not the fix. It changes no threshold and
decides nothing; the card is assigned, and scaling the constants belongs to
whoever holds it.

  docker compose --env-file .env.docker run --rm --no-deps eval \\
      python -m ml.eval.diagnose_detection --test test4 --dump results/test4_ball_track.json
  docker compose --env-file .env.docker run --rm --no-deps eval \\
      python -m ml.eval.track_quality test4

**What it cannot tell you.** `MAX_JUMP_PX` is applied inside `_pick_active`
while tracking, choosing among the detections the model returned for a frame. A
dump holds only the positions that WON, never the candidates that lost — so no
tool reading a dump can simulate a different `MAX_JUMP_PX`. Answering that one
needs a re-track.

Worth being exact about what the threshold does, because it is weaker than
"budget" suggests: when the nearest candidate exceeds it, `_pick_active` does
not reject anything — it returns `detections[0]`, the highest-confidence one,
which is appended unconditionally. So `MAX_JUMP_PX` never removes a position,
and it only changes the outcome for a frame that returned two or more
detections. A sample exceeding the budget therefore says nothing about what a
different threshold would have done, even directionally. The px-per-sample row
below is a distance histogram, not a proxy for the threshold.

Everything below is computed from the surviving track, which is what the
segmentation constants actually see.
"""
import argparse
import logging
from dataclasses import dataclass

import numpy as np

from ml.eval.tune_contacts import load
from ml.pipeline import ball as B

# "Not moving" for a detector whose jitter the segmentation config puts at
# ~0-5 px/s at any resolution. Deliberately above that band rather than at
# zero: a locked-on detection wanders by a pixel or two per sample, so an exact
# zero would count almost none of them.
STATIONARY_PXPS = 10.0


@dataclass(frozen=True)
class Sample:
    """One consecutive pair of positions, as the segmenter sees it."""

    dt: float
    pxps: float


def consecutive_speeds(track, max_gap: float = B.MAX_SAMPLE_GAP_SEC) -> list[Sample]:
    """Speeds between consecutive samples, skipping detection gaps.

    Pairs spanning more than `max_gap` are dropped rather than counted as a
    huge jump — the same rule `_segment_track` and `bridge_windows_by_motion`
    apply, so this describes the track those two are looking at.
    """
    out: list[Sample] = []
    for a, b in zip(track.positions, track.positions[1:]):
        dt = b.time - a.time
        if dt <= 0 or dt > max_gap:
            continue
        out.append(Sample(dt, float(np.hypot(b.x - a.x, b.y - a.y)) / dt))
    return out


def fraction(samples: list[Sample], predicate) -> float:
    """Share of samples satisfying `predicate`, which receives the whole Sample.

    It takes the Sample rather than the speed on purpose. The first version
    passed `s.pxps` alone, which put `dt` out of reach — and the px-per-sample
    row below then reached for `MAX_SAMPLE_GAP_SEC` instead, which is the
    maximum PERMITTED gap (1.0s), not the sample's own step (0.1s at the
    shipped cadence). That inflated the row 10x and it read as a plausible
    number rather than an error.
    """
    if not samples:
        return 0.0
    return sum(1 for s in samples if predicate(s)) / len(samples)


def _row(label: str, value: float, *, note: str = "") -> str:
    """One aligned percentage row. A measurement no one can read is not one."""
    line = f"  {label:<36}{value:7.1%}"
    return f"{line}   <- {note}" if note else line


def report(track, frame_height: int) -> str:
    samples = consecutive_speeds(track)
    if not samples:
        return "no usable consecutive samples in this dump"

    speeds = np.array([s.pxps for s in samples])
    # frame-heights/s is the resolution-independent view, and the one the
    # constants' comments argue in: real play sits at 0.3-0.9 fh/s, and test4's
    # distant camera at ~0.1.
    fh = np.array(speeds) / frame_height if frame_height > 0 else None

    scaled_ceiling = B.SEG_MAX_SPEED_PXPS * (frame_height / B.REFERENCE_FRAME_HEIGHT)

    out = [
        f"frame_height={frame_height}  samples={len(samples)}  "
        f"positions={len(track.positions)}",
        "",
        "SPEED BETWEEN CONSECUTIVE SAMPLES",
        f"  median            {np.median(speeds):9.1f} px/s"
        + (f"   {np.median(fh):6.3f} fh/s" if fh is not None else ""),
        f"  p90               {np.percentile(speeds, 90):9.1f} px/s"
        + (f"   {np.percentile(fh, 90):6.3f} fh/s" if fh is not None else ""),
        "",
        "THE LOCK-ON SIGNATURE  (CF-229's acceptance number)",
        _row(
            f"under {STATIONARY_PXPS:.0f} px/s",
            fraction(samples, lambda s: s.pxps < STATIONARY_PXPS),
            note="'materially below 50%' is the target",
        ),
        _row(
            f"under {B.SEG_MIN_MEDIAN_SPEED_PXPS:.0f} px/s",
            fraction(samples, lambda s: s.pxps < B.SEG_MIN_MEDIAN_SPEED_PXPS),
            note="the held/spare-ball filter's own floor",
        ),
        "",
        "WHAT THE SEGMENTATION CEILING SPLITS",
        _row(
            f"over {B.SEG_MAX_SPEED_PXPS:.0f} px/s (shipped, unscaled)",
            fraction(samples, lambda s: s.pxps > B.SEG_MAX_SPEED_PXPS),
        ),
        _row(
            f"over {scaled_ceiling:.0f} px/s (if it scaled)",
            fraction(samples, lambda s: s.pxps > scaled_ceiling),
        ),
        _row(
            f"over {B.MAX_JUMP_PX:.0f} px in one sample",
            fraction(samples, lambda s: s.pxps * s.dt > B.MAX_JUMP_PX),
            note="a distance histogram, NOT the threshold — see below",
        ),
    ]

    split_now = fraction(samples, lambda s: s.pxps > B.SEG_MAX_SPEED_PXPS)
    split_scaled = fraction(samples, lambda s: s.pxps > scaled_ceiling)
    out += [
        "",
        f"Scaling the ceiling would stop splitting {split_now - split_scaled:.1%} of "
        "samples as bogus track hops.",
        "Whether that is a gain depends on what the recovered samples ARE, which "
        "is what the",
        "lock-on fraction above answers: merged stationary detections drag a "
        "segment's median under",
        "the held-ball filter and the fixture collapses to zero contacts. Both "
        "numbers, or neither.",
        "",
        "The px-in-one-sample row is a distance histogram and NOT a simulation "
        "of MAX_JUMP_PX.",
        "That threshold picks among detections during tracking and a dump holds "
        "only the ones",
        "that won; it also never DROPS a position — over budget, _pick_active "
        "falls back to the",
        "highest-confidence detection and appends it anyway. Answering it needs "
        "a re-track.",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Track-quality diagnostic for CF-229 (#233)."
    )
    ap.add_argument("test_id", nargs="?", default="test4",
                    help="fixture id whose ball-track dump to read (default test4)")
    args = ap.parse_args(argv)

    logging.disable(logging.INFO)
    track, _positions, frame_h, _fx = load(args.test_id)
    print(report(track, frame_h))


if __name__ == "__main__":
    main()
