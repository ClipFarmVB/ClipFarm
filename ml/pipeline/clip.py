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


def generate_clips(
    video_path: str,
    detections: list[dict],
    output_dir: Path,
) -> list[dict]:
    """
    Cut clips and extract thumbnails for each detection.

    Returns extended detection dicts with keys:
      clip_path, thumb_path (may be None on failure)
    """
    try:
        import ffmpeg
    except ImportError:
        logger.warning("ffmpeg-python not installed — skipping clip generation")
        return []

    results = []
    for det in detections:
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
                .input(video_path, ss=start, t=duration)
                .output(
                    str(clip_path),
                    vcodec="libx264",
                    preset="fast",
                    crf=23,
                    acodec="aac",
                    movflags="+faststart",
                    pix_fmt="yuv420p",
                    loglevel="error",
                )
                .overwrite_output()
                .run()
            )
        except Exception:
            logger.exception("Failed to cut clip for detection at %.1f", start)
            continue

        # ── Extract thumbnail ─────────────────────────────────────────────────
        thumb_ok = False
        try:
            (
                ffmpeg
                .input(video_path, ss=mid)
                .output(str(thumb_path), vframes=1, loglevel="error")
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

    return results


def generate_condensed_video(
    video_path: str,
    windows: list[tuple[float, float]],
    output_dir: Path,
) -> tuple[Path, Path | None, float]:
    """
    Cut each keep-window and stitch them into one condensed video.

    Two-step on purpose: each window is re-encoded with identical stream
    parameters, then joined with the concat demuxer in stream-copy mode.
    A single filter_complex trim/concat would decode the dead time being
    discarded and fail atomically on any bad edge; per-part encoding only
    touches kept footage and lets one bad window be skipped.

    Returns (condensed_path, thumb_path or None, condensed_duration).
    Raises RuntimeError if no window could be cut.
    """
    import ffmpeg

    parts_dir = output_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    part_paths: list[Path] = []

    for i, (start, end) in enumerate(windows):
        part_path = parts_dir / f"part_{i:04d}.mp4"
        try:
            (
                ffmpeg
                .input(video_path, ss=start, t=end - start)
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
                    loglevel="error",
                )
                .overwrite_output()
                .run()
            )
            part_paths.append(part_path)
        except Exception:
            logger.exception("Failed to cut condense window %.1f–%.1f", start, end)

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

    thumb_path = output_dir / "condensed.jpg"
    thumb_ok = False
    try:
        first_start, first_end = windows[0]
        (
            ffmpeg
            .input(video_path, ss=(first_start + first_end) / 2)
            .output(str(thumb_path), vframes=1, loglevel="error")
            .overwrite_output()
            .run()
        )
        thumb_ok = True
    except Exception:
        logger.warning("Thumbnail extraction failed for condensed video")

    logger.info(
        "Condensed video: %d/%d windows stitched, %.1fs total",
        len(part_paths), len(windows), condensed_duration,
    )
    return condensed_path, thumb_path if thumb_ok else None, condensed_duration


def recut_single(
    video_path: str,
    start: float,
    end: float,
    output_dir: Path,
) -> tuple[Path, Path | None]:
    """
    Re-cut a single clip from the source video.
    Returns (clip_path, thumb_path or None).
    """
    import ffmpeg

    clip_path = output_dir / "recut.mp4"
    thumb_path = output_dir / "recut.jpg"
    duration = end - start
    mid = start + duration / 2

    (
        ffmpeg
        .input(video_path, ss=start, t=duration)
        .output(
            str(clip_path),
            vcodec="libx264",
            preset="fast",
            crf=23,
            acodec="aac",
            movflags="+faststart",
            pix_fmt="yuv420p",
            loglevel="error",
        )
        .overwrite_output()
        .run()
    )

    thumb_ok = False
    try:
        (
            ffmpeg
            .input(video_path, ss=mid)
            .output(str(thumb_path), vframes=1, loglevel="error")
            .overwrite_output()
            .run()
        )
        thumb_ok = True
    except Exception:
        logger.warning("Thumbnail extraction failed for recut at %.1f", mid)

    return clip_path, thumb_path if thumb_ok else None
