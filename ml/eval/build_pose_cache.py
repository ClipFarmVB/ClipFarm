"""
Build the per-fixture pose keypoint cache the dead-time pose signal reads (CF-198).

Same shape as `ball_caches/`, and for the same reason: pose inference over a
full game is the expensive part, the fixtures never change, so pay for it once
and let every later experiment be free interval arithmetic. Caches are keyed by
the fixture's `source_video_md5` — game rows get deleted, bytes do not — and
gitignored, like the ball caches.

Raw keypoints are cached rather than a pre-derived activity scalar. Player
motion magnitude is the first feature this card measures, but occupancy
geometry and player-count are the obvious follow-ups, and deriving those from a
cache costs nothing while re-running inference costs a GPU pass per idea.

    python -m ml.eval.build_pose_cache --test test4
    python -m ml.eval.build_pose_cache --all --device mps

Videos resolve locally first (by content MD5 over the repo root, so a file that
was renamed still matches) and fall back to downloading `source_r2_key`. The
fallback needs the app deps and R2 credentials; the local path needs neither.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from ml.eval.harness import load_deadtime_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
POSE_CACHE_DIR = Path(__file__).resolve().parent / "pose_caches"

logger = logging.getLogger("build_pose_cache")


def _md5(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def find_local_video(md5: str, search_root: Path = REPO_ROOT) -> Path | None:
    """
    A local .mp4 whose contents hash to md5, or None.

    Matched by content, not filename: the fixtures name files like
    `DeadtimeLabel4.mp4`, but the hash is the pin and a renamed or re-downloaded
    copy should still hit. Hashing a few GB is seconds against the minutes of
    GPU time it saves, and top-level only — deep trees here hold model outputs
    and condensed cuts, not sources.
    """
    for candidate in sorted(search_root.glob("*.mp4")):
        if _md5(candidate) == md5:
            return candidate
    return None


def build(
    test_id: str,
    *,
    sample_fps: float,
    model_name: str,
    imgsz: int,
    device: str | None,
    force: bool = False,
) -> Path:
    from ml.pipeline.detect import extract_keypoints

    fixture = load_deadtime_fixture(test_id)
    md5 = fixture.raw["source_video_md5"]
    out = POSE_CACHE_DIR / f"{md5}.json"
    if out.exists() and not force:
        print(f"{test_id}: cache already at {out} (--force to rebuild)")
        return out

    local = find_local_video(md5)
    with tempfile.TemporaryDirectory() as tmpdir:
        if local is None:
            r2_key = fixture.raw.get("source_r2_key")
            if not r2_key:
                raise SystemExit(
                    f"{test_id}: no local video hashes to {md5} and the fixture has no "
                    "source_r2_key, so there is nothing to run pose over. Put the source "
                    f"file ({fixture.raw.get('source_video_file', '?')}) in {REPO_ROOT}."
                )
            # Lazy: the local path must work on a laptop with no app deps and no
            # R2 credentials, which is the common case for four of five fixtures.
            from app.services import storage as s3

            local = Path(tmpdir) / "game.mp4"
            print(f"{test_id}: no local copy — downloading {r2_key} from R2...")
            s3.download_file(r2_key, local)

        print(f"{test_id}: pose pass over {local.name} ({fixture.duration:.0f}s)")
        started = time.monotonic()
        cache = extract_keypoints(
            str(local),
            model_name=model_name,
            imgsz=imgsz,
            sample_fps=sample_fps,
            device=device,
        )

    # Wall-clock is recorded with the cache, not just printed: the card has to
    # answer what a full-video pose pass costs, and a number found later in a
    # terminal scrollback is a number nobody can check.
    cache["elapsed_seconds"] = round(time.monotonic() - started, 1)
    cache["device"] = device or "auto"
    cache["test_id"] = test_id
    cache["source_video_md5"] = md5

    POSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cache))
    dur = cache["duration"] or 1.0
    print(
        f"{test_id}: {len(cache['samples'])} samples -> {out} "
        f"({out.stat().st_size / 1e6:.1f} MB, {cache['elapsed_seconds']:.0f}s wall, "
        f"{cache['elapsed_seconds'] / dur:.2f}x realtime)"
    )
    return out


def load_pose_cache(md5: str) -> dict:
    """The cached keypoint pass for a video, or a readable exit."""
    path = POSE_CACHE_DIR / f"{md5}.json"
    if not path.exists():
        raise SystemExit(
            f"pose cache {path} missing — build it with "
            f"python -m ml.eval.build_pose_cache --test <test_id>"
        )
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--test", help="fixture id, e.g. test4")
    src.add_argument("--all", action="store_true", help="every fixture below")
    ap.add_argument("--tests", default="test1,test2,test3,test4,test5",
                    help="fixture ids --all covers")
    ap.add_argument("--sample-fps", type=float, default=3.0,
                    help="pose samples/s (default 3.0 — matches the ball track's rate)")
    ap.add_argument("--model", default="yolov8s-pose.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=None,
                    help="torch device: mps on Apple silicon, cuda, cpu (default: auto)")
    ap.add_argument("--force", action="store_true", help="rebuild an existing cache")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tests = args.tests.split(",") if args.all else [args.test]
    for test_id in tests:
        build(
            test_id.strip(),
            sample_fps=args.sample_fps,
            model_name=args.model,
            imgsz=args.imgsz,
            device=args.device,
            force=args.force,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
