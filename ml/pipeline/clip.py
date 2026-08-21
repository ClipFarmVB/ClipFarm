"""
FFmpeg clip extraction and thumbnail generation.

For each detection dict {start, end, action, confidence},
cuts the clip from the source video and extracts a thumbnail
at the midpoint.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# ffmpeg sizes its thread pools from the visible core count, which inside a
# container is the HOST's, not what the cgroup allows. On a 16-core host x264
# defaults to ~24 encoder threads and allocates per-thread frame buffers —
# hundreds of MB at 1080p — which OOM-killed the 512 MB worker on its first
# production run (CF-224). Every encode and decode below pins the count.
#
# Callers pass the deployed value (api Settings.ffmpeg_threads), since the right
# number depends on the instance plan — production runs 1 on a 0.5-CPU box. This
# is the fallback for a bare pipeline call: low enough for any container, since
# the whole failure mode is a default sized by the wrong machine.
DEFAULT_THREADS = 2


def _report(on_progress, fraction: float) -> None:
    """Invoke a progress callback; reporting must never break the cut."""
    if on_progress is None:
        return
    try:
        on_progress(fraction)
    except Exception:
        logger.warning("Progress callback failed", exc_info=True)


def generate_clips(
    video_path: str,
    detections: list[dict],
    output_dir: Path,
    on_progress=None,
    threads: int = DEFAULT_THREADS,
) -> list[dict]:
    """
    Cut clips and extract thumbnails for each detection.

    on_progress, when given, is called with the fraction of detections
    processed (0-1) after each one; callback errors are swallowed.

    threads bounds both the decoder and the x264 encoder — see DEFAULT_THREADS.

    Returns extended detection dicts with keys:
      clip_path, thumb_path (may be None on failure)
    """
    try:
        import ffmpeg
    except ImportError:
        logger.warning("ffmpeg-python not installed — skipping clip generation")
        return []

    results = []
    for det_idx, det in enumerate(detections):
        clip_id = uuid.uuid4()
        clip_path = output_dir / f"{clip_id}.mp4"
        thumb_path = output_dir / f"{clip_id}.jpg"

        start = det["start"]
        duration = det["end"] - det["start"]
        mid = start + duration / 2

        # ── Cut clip ──────────────────────────────────────────────────────────
        try:
            (
                ffmpeg
                .input(video_path, ss=start, t=duration, threads=threads)
                .output(
                    str(clip_path),
                    vcodec="libx264",
                    preset="fast",
                    crf=23,
                    acodec="aac",
                    movflags="+faststart",
                    pix_fmt="yuv420p",
                    threads=threads,
                    loglevel="error",
                )
                .overwrite_output()
                .run()
            )
        except Exception:
            logger.exception("Failed to cut clip for detection at %.1f", start)
            _report(on_progress, (det_idx + 1) / len(detections))
            continue

        # ── Extract thumbnail ─────────────────────────────────────────────────
        thumb_ok = False
        try:
            (
                ffmpeg
                .input(video_path, ss=mid, threads=threads)
                .output(str(thumb_path), vframes=1, threads=threads, loglevel="error")
                .overwrite_output()
                .run()
            )
            thumb_ok = True
        except Exception:
            logger.warning("Thumbnail extraction failed for clip at %.1f", mid)

        results.append({
            **det,
            "clip_path": clip_path,
            "thumb_path": thumb_path if thumb_ok else None,
        })
        _report(on_progress, (det_idx + 1) / len(detections))

    return results


def generate_condensed_video(
    video_path: str,
    windows: list[tuple[float, float]],
    output_dir: Path,
    on_progress=None,
    threads: int = DEFAULT_THREADS,
) -> tuple[Path, float]:
    """
    Cut each keep-window and stitch them into one condensed video.

    Two-step on purpose: each window is re-encoded with identical stream
    parameters, then joined with the concat demuxer in stream-copy mode.
    A single filter_complex trim/concat would decode the dead time being
    discarded and fail atomically on any bad edge; per-part encoding only
    touches kept footage and lets one bad window be skipped.

    on_progress, when given, is called with the fraction of windows encoded
    (0-1) after each part; the final stream-copy stitch is near-free and
    not reported. Callback errors are swallowed.

    threads bounds both the decoder and the x264 encoder — see DEFAULT_THREADS.
    It matters at least as much here as in generate_clips: condensing re-encodes
    every kept second, not one window.

    Returns (condensed_path, condensed_duration).
    Raises RuntimeError if no window could be cut.
    """
    import ffmpeg

    parts_dir = output_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    part_paths: list[Path] = []

    # Report progress by kept-seconds encoded, not window count: windows vary
    # widely in length, so count-based progress lurches on a long window. Each
    # part's encode time is ~proportional to its duration, so this tracks the
    # real work and keeps the bar (and its ETA) smooth.
    total_kept = sum(end - start for start, end in windows) or 1.0
    encoded = 0.0

    for i, (start, end) in enumerate(windows):
        part_path = parts_dir / f"part_{i:04d}.mp4"
        try:
            (
                ffmpeg
                .input(video_path, ss=start, t=end - start, threads=threads)
                .output(
                    str(part_path),
                    vcodec="libx264",
                    preset="fast",
                    crf=23,
                    acodec="aac",
                    # Copy-concat needs identical streams across parts: pin the
                    # audio sample rate and regenerate timestamps per part so
                    # joins don't glitch or drift.
                    ar=48000,
                    avoid_negative_ts="make_zero",
                    movflags="+faststart",
                    pix_fmt="yuv420p",
                    threads=threads,
                    loglevel="error",
                )
                .overwrite_output()
                .run()
            )
            part_paths.append(part_path)
        except Exception:
            logger.exception("Failed to cut condense window %.1f–%.1f", start, end)
        encoded += end - start
        _report(on_progress, encoded / total_kept)

    if not part_paths:
        raise RuntimeError("All condense windows failed to cut")

    list_file = parts_dir / "concat.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in part_paths))

    condensed_path = output_dir / "condensed.mp4"
    (
        ffmpeg
        .input(str(list_file), format="concat", safe=0)
        .output(
            str(condensed_path),
            c="copy",
            movflags="+faststart",
            fflags="+genpts",
            loglevel="error",
        )
        .overwrite_output()
        .run()
    )

    # Parts can add up to GBs — drop them as soon as the stitch lands.
    for p in part_paths:
        p.unlink(missing_ok=True)

    try:
        probe = ffmpeg.probe(str(condensed_path))
        condensed_duration = float(probe["format"]["duration"])
    except Exception:
        condensed_duration = sum(end - start for start, end in windows)

    logger.info(
        "Condensed video: %d/%d windows stitched, %.1fs total",
        len(part_paths), len(windows), condensed_duration,
    )
    return condensed_path, condensed_duration


def recut_single(
    video_path: str,
    start: float,
    end: float,
    output_dir: Path,
    threads: int = DEFAULT_THREADS,
) -> tuple[Path, Path | None]:
    """
    Re-cut a single clip from the source video.

    threads bounds both the decoder and the x264 encoder — see DEFAULT_THREADS.

    Returns (clip_path, thumb_path or None).
    """
    import ffmpeg

    clip_path = output_dir / "recut.mp4"
    thumb_path = output_dir / "recut.jpg"
    duration = end - start
    mid = start + duration / 2

    (
        ffmpeg
        .input(video_path, ss=start, t=duration, threads=threads)
        .output(
            str(clip_path),
            vcodec="libx264",
            preset="fast",
            crf=23,
            acodec="aac",
            movflags="+faststart",
            pix_fmt="yuv420p",
            threads=threads,
            loglevel="error",
        )
        .overwrite_output()
        .run()
    )

    thumb_ok = False
    try:
        (
            ffmpeg
            .input(video_path, ss=mid, threads=threads)
            .output(str(thumb_path), vframes=1, threads=threads, loglevel="error")
            .overwrite_output()
            .run()
        )
        thumb_ok = True
    except Exception:
        logger.warning("Thumbnail extraction failed for recut at %.1f", mid)

    return clip_path, thumb_path if thumb_ok else None
