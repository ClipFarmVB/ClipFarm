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
# number depends on the instance plan. Production runs 1 on a whole CPU
# (`standard`, CF-240) — that 1 was measured at 0.5 CPU under CF-224 and carried
# over unchanged when the plan grew, so it is a known-safe value rather than a
# tuned one; CF-223 re-measures it at the new size. This is the fallback for a
# bare pipeline call: low enough for any container, since the whole failure mode
# is a default sized by the wrong machine.
DEFAULT_THREADS = 2

# CF-321: the phone rendition. Clips are encoded at source resolution, so a 4K
# upload yields 4K clips and a phone on cellular pays for every pixel it cannot
# see. One fixed rendition rather than an HLS ladder — these clips are seconds
# long, and adaptive streaming is a lot of machinery for that.
#
# The bound is on the SHORT side, not the height: a game filmed in portrait is
# 1080x1920, and clamping *height* there would hand back a 405px-wide video.
# Short-side 720 means 1920x1080 -> 1280x720 and 1080x1920 -> 720x1280, which is
# what "720p" is normally taken to mean on both orientations.
MOBILE_SHORT_SIDE = 720

# Higher than the 23 the full-size clip uses. The rendition is watched on a
# phone-sized screen where the extra detail is not resolvable, and bandwidth is
# the entire point of the ticket — see the measured deltas on CF-321 (#371).
MOBILE_CRF = 26


def _mobile_scale_filter(short_side: int = MOBILE_SHORT_SIDE) -> str:
    """Build the scale filter for the phone rendition.

    Expressed in ffmpeg's own `iw`/`ih` rather than in numbers computed from a
    probe, for two reasons that both bite on real phone footage:

    * `iw`/`ih` are read **after** autorotation, so a portrait clip recorded as
      1920x1080-plus-a-rotate-flag scales by the dimensions it actually has.
      Explicit `scale=1280:720` on that frame stretches it.
    * `min()` makes an upscale unrepresentable rather than merely unlikely, so
      a source smaller than the bound can never be blown up by a bad branch.

    `-2` on the free axis keeps the aspect ratio and rounds to an even number,
    which `yuv420p` requires — an odd dimension fails the encode outright.
    """
    return (
        f"scale='if(gt(iw,ih),-2,min({short_side},iw))'"
        f":'if(gt(iw,ih),min({short_side},ih),-2)'"
    )


def _needs_mobile_rendition(
    width: int, height: int, short_side: int = MOBILE_SHORT_SIDE
) -> bool:
    """Is this clip big enough that a phone rendition saves anything?

    Compares the short side, which makes the answer independent of rotation:
    `min(w, h)` is the same whether the probe reports 1080x1920 or 1920x1080
    with a rotate flag, so this never has to interpret that metadata.

    A clip already at or under the bound gets no second file. Re-encoding it at
    the same resolution would cost storage and a generation loss to save
    nothing, and NULL is a perfectly good answer — the client falls back to the
    full-size URL, which for a clip this small is already the phone rendition.
    """
    if width <= 0 or height <= 0:
        return False
    return min(width, height) > short_side


def _probe_dimensions(ffmpeg, video_path: str) -> tuple[int, int] | None:
    """Dimensions of the first video stream, or None if they can't be read."""
    try:
        probe = ffmpeg.probe(str(video_path))
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                return int(stream["width"]), int(stream["height"])
    except Exception:
        logger.warning("Could not probe dimensions of %s", video_path, exc_info=True)
    return None


def _encode_mobile_rendition(
    ffmpeg, clip_path: Path, output_path: Path, threads: int, short_side: int
) -> Path | None:
    """Transcode a cut clip down to the phone rendition.

    Reads the **cut clip**, not the source video. The clip is seconds long and
    already on local disk, where the source is up to 8 GB and would have to be
    sought and decoded a second time. It also keeps this independent of how the
    clip was produced, which matters because CF-239 (#242) is about to make that
    conditional: it stream-copies when the upload is already H.264/AAC, so the
    clip is browser-safe H.264 on both paths and this encode reads it the same
    way either way.

    Returns None on failure. A missing rendition costs a phone some bandwidth;
    failing the clip costs the user the clip.
    """
    try:
        (
            ffmpeg
            .input(str(clip_path), threads=threads)
            .output(
                str(output_path),
                vf=_mobile_scale_filter(short_side),
                vcodec="libx264",
                preset="fast",
                crf=MOBILE_CRF,
                acodec="aac",
                movflags="+faststart",
                pix_fmt="yuv420p",
                threads=threads,
                loglevel="error",
            )
            .overwrite_output()
            .run()
        )
        return output_path
    except Exception:
        logger.warning(
            "Phone rendition failed for %s — serving the full-size clip only",
            clip_path, exc_info=True,
        )
        return None


def _bounded(threads: int) -> int:
    """Refuse a thread count that is not a bound.

    Settings.ffmpeg_threads rejects these at boot, but this module is called
    directly too (eval, scripts), and the two bad values fail in ways that do
    not look like a bad argument: 0 is ffmpeg's *auto* sentinel, so it quietly
    restores the host-sized pool, and a negative fails every cut inside the
    swallowing `except` below — a game with no clips and no obvious cause.
    """
    if threads < 1:
        logger.warning(
            "ffmpeg threads=%s is not a usable bound — falling back to %d",
            threads, DEFAULT_THREADS,
        )
        return DEFAULT_THREADS
    return threads


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
    mobile_short_side: int = MOBILE_SHORT_SIDE,
) -> list[dict]:
    """
    Cut clips and extract thumbnails for each detection.

    on_progress, when given, is called with the fraction of detections
    processed (0-1) after each one; callback errors are swallowed.

    threads bounds both the decoder and the x264 encoder — see DEFAULT_THREADS.

    Returns extended detection dicts with keys:
      clip_path, thumb_path (may be None on failure),
      mobile_path (may be None — see _needs_mobile_rendition)
    """
    try:
        import ffmpeg
    except ImportError:
        logger.warning("ffmpeg-python not installed — skipping clip generation")
        return []

    threads = _bounded(threads)

    # Probed once per game rather than once per clip: clips are cut without a
    # scale filter, so every one of them has the source's dimensions, and the
    # probe is a process spawn we would otherwise pay for on each detection.
    dims = _probe_dimensions(ffmpeg, video_path)
    if dims is None:
        # Fail open. The scale filter cannot upscale, so the cost of guessing
        # wrong here is one wasted encode; the cost of guessing wrong the other
        # way is a phone streaming a 4K clip, which is the bug being fixed.
        want_mobile = True
        logger.info("Source dimensions unknown — encoding phone renditions anyway")
    else:
        want_mobile = _needs_mobile_rendition(*dims, mobile_short_side)
        if not want_mobile:
            logger.info(
                "Source is %dx%d, at or under the %dpx phone bound — no rendition",
                dims[0], dims[1], mobile_short_side,
            )

    results = []
    for det_idx, det in enumerate(detections):
        clip_id = uuid.uuid4()
        clip_path = output_dir / f"{clip_id}.mp4"
        thumb_path = output_dir / f"{clip_id}.jpg"
        mobile_path = output_dir / f"{clip_id}.mobile.mp4"

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

        # ── Phone rendition ───────────────────────────────────────────────────
        mobile_out = None
        if want_mobile:
            mobile_out = _encode_mobile_rendition(
                ffmpeg, clip_path, mobile_path, threads, mobile_short_side,
            )

        results.append({
            **det,
            "clip_path": clip_path,
            "thumb_path": thumb_path if thumb_ok else None,
            "mobile_path": mobile_out,
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

    threads = _bounded(threads)
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
    mobile_short_side: int = MOBILE_SHORT_SIDE,
) -> tuple[Path, Path | None, Path | None]:
    """
    Re-cut a single clip from the source video.

    threads bounds both the decoder and the x264 encoder — see DEFAULT_THREADS.

    Returns (clip_path, thumb_path or None, mobile_path or None).

    The phone rendition is re-made here rather than left alone, because the
    storage keys are deterministic per clip id: a trim that rewrote the clip and
    not its rendition would leave the old boundaries sitting at the rendition's
    key, and mobile clients would keep playing the untrimmed cut. A stale
    rendition is a worse answer than no rendition.
    """
    import ffmpeg

    threads = _bounded(threads)
    clip_path = output_dir / "recut.mp4"
    thumb_path = output_dir / "recut.jpg"
    mobile_path = output_dir / "recut.mobile.mp4"
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

    dims = _probe_dimensions(ffmpeg, video_path)
    mobile_out = None
    if dims is None or _needs_mobile_rendition(*dims, mobile_short_side):
        mobile_out = _encode_mobile_rendition(
            ffmpeg, clip_path, mobile_path, threads, mobile_short_side,
        )

    return clip_path, thumb_path if thumb_ok else None, mobile_out
