"""
AI labeller tooling for in-play spans (CF-594).

Hand-labelling a game's rallies takes a person an hour per game, and detection
work keeps waiting on it. This module is the mechanical half of a labelling
pass that needs no person watching the game: ffmpeg pulls frames and contact
sheets off the raw video, Claude reads them in the session (vision, no paid
compute), and the span list it writes down comes back through the draft writer
below as a dead-time fixture that is *marked as an AI draft*.

Three rules the writer enforces rather than documents:

  - a draft always carries `labeler: "claude-vision"` and `labeling: "ai-draft"`,
    and every span a confidence; nothing here can produce a file that reads as
    a human label pass.
  - a span the labeller was unsure about (confidence below REVIEW_BELOW) is
    flagged `review: true` whatever the caller passed, so the person's job is
    spot-checking the flagged spans, not re-watching the game.
  - drafts go to fixtures/drafts/, never into fixtures/ itself. The pinning
    suite (ml/tests/test_eval_fixtures.py) globs fixtures/*_deadtime.json
    non-recursively, so a draft is invisible to it until a person reviews it
    and moves it up a level — and `write_draft` refuses a path in fixtures/
    or on top of a file that is not already an AI draft.

Validation before trust: label test2–5 blind, then `score` the draft against
the human fixture (seconds-level agreement via metrics.evaluate_deadtime, the
same numbers the condense harness reports). Rally-level recall/precision and
per-boundary error wait on CF-593's matcher. The protocol, the agreement bar
and the validation status live in docs/research/ai-labeller.md.

Stdlib-only at import, like metrics.py: ffmpeg/ffprobe run as subprocesses
(the idiom in ml/pipeline/audio.py), and the harness is imported lazily (inside
`score_draft` and `_parse_ts`) so the frame and sheet commands never touch it.

Usage:
  python -m ml.eval.ai_label probe  VIDEO
  python -m ml.eval.ai_label md5    VIDEO
  python -m ml.eval.ai_label locate --test test2            # by md5, in $CLIPFARM_FIXTURE_VIDEO_DIR
  python -m ml.eval.ai_label locate --test game7            # no fixture: game7.mp4 in $CLIPFARM_EXTRA_VIDEO_DIR
  python -m ml.eval.ai_label sheets VIDEO [--every 1.0] [--cols 6 --rows 5]
  python -m ml.eval.ai_label frames VIDEO [--every 1.0] [--start S --end E]
  python -m ml.eval.ai_label frames VIDEO --around 754 [--radius 4 --every 0.5]
  python -m ml.eval.ai_label write  --test game7 --video VIDEO --spans spans.json
  python -m ml.eval.ai_label score  --test test2 [--draft PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ml.eval.metrics import DeadTimeSignals

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVAL_DIR / "fixtures"
DRAFTS_DIR = FIXTURES_DIR / "drafts"
FRAMES_DIR = EVAL_DIR / "frames"          # gitignored scratch for frames and sheets

# Where the raw videos live locally. Neither variable exists anywhere else in
# the repo; this module introduces the convention, and `locate` is the one
# command that reads either. Fixture videos are found by content MD5 (the
# fixture's `source_video_md5`), not by name, so a renamed download still
# resolves — and a re-encoded one correctly does not. An unlabelled game has
# no fixture to carry an md5, so it is found by name instead: the file's stem
# is the id the draft will be written under (game7.mp4 -> `--test game7`).
FIXTURE_VIDEO_DIR_ENV = "CLIPFARM_FIXTURE_VIDEO_DIR"   # the test2–5 sources
EXTRA_VIDEO_DIR_ENV = "CLIPFARM_EXTRA_VIDEO_DIR"       # unlabelled local games

LABELER = "claude-vision"
LABELING = "ai-draft"

# A span below this confidence is flagged for human review whatever the caller
# said. A constant, not a setting: the point of the flag is that the reviewer
# can rely on it meaning the same thing in every draft. 0.7 is justified in
# docs/research/ai-labeller.md and is meant to be revisited once the
# validation run says how confidence relates to boundary error.
REVIEW_BELOW = 0.7

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm")

DRAFT_NOTE = (
    "AI DRAFT — NOT GROUND TRUTH. Spans were read off ffmpeg frames and contact "
    "sheets by Claude (vision) in a session, per docs/research/ai-labeller.md, "
    "and have not been reviewed by a person. Spans with review=true are the "
    "ones the labeller was unsure of; check those first. This file has no "
    "source_r2_key, so `harness --mode deadtime --offline` cannot run on it; "
    "score it with `python -m ml.eval.ai_label score`. Do not move it into "
    "fixtures/ or change `labeling` without a human review pass."
)


# ── ffprobe / ffmpeg ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class VideoInfo:
    duration_sec: float
    width: int
    height: int
    fps: float


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Run ffmpeg/ffprobe; raise with the tail of stderr on failure."""
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"{cmd[0]} failed ({result.returncode}): {tail}")
    return result


def _parse_rate(text: str | None) -> float:
    """ffprobe writes frame rates as a ratio ('30000/1001'); '0/0' means unknown."""
    if not text:
        return 0.0
    num, _, den = text.partition("/")
    try:
        n = float(num)
        d = float(den) if den else 1.0
    except ValueError:
        return 0.0
    return n / d if d else 0.0


def probe(video: Path) -> VideoInfo:
    """Duration, frame size and frame rate of the first video stream."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "v:0", str(video),
    ]
    data = json.loads(_run(cmd, timeout=60).stdout.decode("utf-8"))
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"{video}: ffprobe found no video stream")
    stream = streams[0]
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    if duration is None:
        raise RuntimeError(f"{video}: ffprobe reported no duration")
    return VideoInfo(
        duration_sec=float(duration),
        width=int(stream.get("width", 0)),
        height=int(stream.get("height", 0)),
        fps=_parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate")),
    )


def content_md5(path: Path) -> str:
    """Content hash of a file, streamed in 1 MiB chunks.

    Mirrors `_file_md5` in api/app/workers/tasks.py byte for byte, so the value
    equals the fixture's `source_video_md5` and the ball-cache key. Copied
    rather than imported: ml/ never imports api/.
    """
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_videos(video_dir: Path) -> list[Path]:
    return sorted(
        p for p in video_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def find_video_by_md5(md5: str, video_dir: Path) -> Path | None:
    """The file in `video_dir` whose content hash is `md5`, or None.

    Hashes every video in the directory, so it costs one read of each file;
    a fixture dir holds a handful of games, which is fine.
    """
    for path in list_videos(video_dir):
        if content_md5(path) == md5.lower():
            return path
    return None


def find_video_by_name(test_id: str, video_dir: Path) -> Path | None:
    """The video in `video_dir` whose stem is `test_id` (any VIDEO_SUFFIXES),
    or None. For an unlabelled game, which has no fixture and so no md5 to
    match: its id is its file name, and the draft is pinned to the bytes by
    md5 only once `write` has read them.

    Raises ValueError when two files share the stem (game7.mp4 and game7.mov):
    guessing between them would pin the draft to a file the frames may not
    have come from.
    """
    matches = [p for p in list_videos(video_dir) if p.stem == test_id]
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} files in {video_dir} are named {test_id}: "
            + ", ".join(p.name for p in matches) + " — rename one, or pass the path directly"
        )
    return matches[0] if matches else None


def video_dir_from_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"{name} is not set. Point it at the directory holding the raw videos "
            f"(see docs/research/ai-labeller.md)."
        )
    path = Path(value).expanduser()
    if not path.is_dir():
        raise SystemExit(f"{name}={value!r} is not a directory")
    return path


# ── frames ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Frame:
    path: Path
    sec: float


def _fmt_ts(seconds: float) -> str:
    """mm:ss, rolling over to h:mm:ss past an hour — the form the human fixtures
    use and a player's seek bar shows. A fractional second keeps one decimal
    ("01:30.5"), which harness.parse_timestamp reads back as 90.5."""
    seconds = round(seconds, 1)
    whole = int(seconds)
    frac = seconds - whole
    if whole >= 3600:
        text = f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
    else:
        text = f"{whole // 60:02d}:{whole % 60:02d}"
    return f"{text}.{int(round(frac * 10))}" if frac >= 0.05 else text


def _frame_name(sec: float) -> str:
    """frame_00754.0s.jpg, frame_00004.5s.jpg, frame_00001.25s.jpg: the exact
    `sec` extract_frames computed (3 dp), trailing zeros dropped. Two distinct
    secs never share a name, so a sub-tenth `every_sec` cannot overwrite a
    frame or list one file twice in frames.json."""
    text = f"{sec:09.3f}".rstrip("0")
    return f"frame_{text}0s.jpg" if text.endswith(".") else f"frame_{text}s.jpg"


def _clear(out_dir: Path, pattern: str) -> None:
    for stale in out_dir.glob(pattern):
        stale.unlink()


def _extract_raw(
    video: Path, out_dir: Path, every_sec: float, start: float, end: float | None, height: int,
) -> list[Path]:
    """One ffmpeg pass: a JPEG per `every_sec` of [start, end), scaled to
    `height` px, as out_dir/_raw_%06d.jpg. Returns the files in order.

    The list, not the duration, is the record of which ticks exist. The fps
    filter emits tick k only when a source frame rounds to it, so the tail
    tick of a fractional-length video (12.0 on a 12.35 s clip) is never
    written; frames.json and sheets.json both list this set, so neither names
    a frame or tile the image does not have.
    """
    _clear(out_dir, "_raw_*.jpg")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += [
        "-i", str(video),
        "-vf", f"fps=1/{every_sec},scale=-2:{height}",
        "-q:v", "3", "-start_number", "0",
        str(out_dir / "_raw_%06d.jpg"),
    ]
    _run(cmd, timeout=3600)
    return sorted(out_dir.glob("_raw_*.jpg"))


def extract_frames(
    video: Path,
    out_dir: Path,
    every_sec: float = 1.0,
    start: float | None = None,
    end: float | None = None,
    height: int = 360,
) -> list[Frame]:
    """One JPEG every `every_sec` seconds of [start, end), scaled to `height` px.

    Files are named by their timestamp (frame_00754.0s.jpg; frame_00001.25s.jpg
    under a sub-tenth step) and `frames.json` beside them lists every (file,
    sec), so the time a frame shows never depends on reading text off the
    image. With the fps filter at 1/N and the seek before the input, output
    frame k sits at start + k*N.
    """
    if every_sec <= 0:
        raise ValueError("every_sec must be positive")
    start = max(0.0, start or 0.0)
    if end is not None and end <= start:
        raise ValueError("end must be after start")
    if not video.is_file():
        raise FileNotFoundError(f"{video}: no such video")
    # Arguments checked, and ffmpeg run, before the previous set is touched: a
    # refused rerun or a failed ffmpeg (corrupt file, timeout) leaves the
    # earlier frames and their frames.json as they were, rather than a
    # sidecar naming files that no longer exist.
    out_dir.mkdir(parents=True, exist_ok=True)
    raws = _extract_raw(video, out_dir, every_sec, start, end, height)
    _clear(out_dir, "frame_*.jpg")   # a rerun replaces the set, or frames.json would not list every file
    frames: list[Frame] = []
    for raw in raws:
        index = int(raw.stem[len("_raw_"):])
        sec = round(start + index * every_sec, 3)
        final = out_dir / _frame_name(sec)
        raw.replace(final)
        frames.append(Frame(path=final, sec=sec))
    sidecar = {
        "video": str(video),
        "start_sec": start,
        "end_sec": end,
        "every_sec": every_sec,
        "height": height,
        "frames": [{"file": f.path.name, "sec": f.sec, "ts": _fmt_ts(f.sec)} for f in frames],
    }
    (out_dir / "frames.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return frames


def dense_frames(
    video: Path,
    around_sec: float,
    out_dir: Path,
    radius: float = 4.0,
    every_sec: float = 0.5,
    height: int = 360,
) -> list[Frame]:
    """Denser frames around a candidate transition (a serve or a point end
    the 1 fps pass could only place to within a second)."""
    return extract_frames(
        video, out_dir, every_sec=every_sec,
        start=max(0.0, around_sec - radius), end=around_sec + radius, height=height,
    )


# ── contact sheets ─────────────────────────────────────────────────────────

_DRAWTEXT_AVAILABLE: bool | None = None


def drawtext_available() -> bool:
    """Whether this ffmpeg was built with the drawtext filter (needs libfreetype).
    Burnt-in timestamps are a reading aid only; the sidecar is the record."""
    global _DRAWTEXT_AVAILABLE
    if _DRAWTEXT_AVAILABLE is None:
        try:
            out = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=30).stdout.decode("utf-8", "replace")
            _DRAWTEXT_AVAILABLE = bool(re.search(r"^\s*\S+\s+drawtext\s", out, re.MULTILINE))
        except (OSError, RuntimeError):
            _DRAWTEXT_AVAILABLE = False
    return _DRAWTEXT_AVAILABLE


def _sheet_filter(tile_height: int, cols: int, rows: int, start: float, burn: bool) -> str:
    parts = []
    if burn:
        # The frame sequence is read at 1/every_sec fps, so each frame's pts
        # is its sample time from the seek point and pts + start is the
        # sidecar's `sec` for that tile.
        parts.append(
            f"drawtext=text='%{{pts\\:hms\\:{start:.3f}}}':x=4:y=4:fontsize={max(12, tile_height // 10)}"
            ":fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=3"
        )
    parts.append(f"tile={cols}x{rows}:padding=2:margin=2:color=black")
    return ",".join(parts)


def contact_sheets(
    video: Path,
    out_dir: Path,
    every_sec: float = 1.0,
    cols: int = 6,
    rows: int = 5,
    tile_height: int = 180,
    start: float | None = None,
    end: float | None = None,
) -> list[Path]:
    """Tile one frame per `every_sec` into cols×rows sheets (30 s per sheet at
    the defaults): one ffmpeg pass pulls the frames, a second tiles them.

    `sheets.json` maps every sheet and tile to its timestamp, so a span can be
    read off a sheet without trusting the burnt-in text (which depends on
    drawtext and a font being present, and is skipped when they are not). It
    lists exactly the frames ffmpeg emitted, which is why the sequence is
    tiled rather than the video in one call: the fps filter does not emit the
    tail tick of a fractional-length video, so a listing derived from the
    duration named one tile more than the last sheet held. Tiles on the last
    sheet past the final frame are black padding and are not listed.
    """
    if every_sec <= 0 or cols <= 0 or rows <= 0:
        raise ValueError("every_sec, cols and rows must be positive")
    info = probe(video)
    start = max(0.0, start or 0.0)
    stop = min(info.duration_sec, end) if end is not None else info.duration_sec
    if stop <= start:
        raise ValueError("end must be after start and inside the video")
    out_dir.mkdir(parents=True, exist_ok=True)
    raws = _extract_raw(video, out_dir, every_sec, start, stop, tile_height)
    _clear(out_dir, "sheet_*.jpg")

    def cmd(burn: bool) -> list[str]:
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "image2", "-framerate", f"1/{every_sec}", "-start_number", "0",
            "-i", str(out_dir / "_raw_%06d.jpg"),
            "-vf", _sheet_filter(tile_height, cols, rows, start, burn),
            "-q:v", "3", "-start_number", "1", str(out_dir / "sheet_%03d.jpg"),
        ]

    burnt = drawtext_available()
    if raws and burnt:
        try:
            _run(cmd(burn=True), timeout=3600)
        except RuntimeError:
            # drawtext present but no usable font (fontconfig): fall back to
            # bare tiles rather than fail — the sidecar carries the times.
            burnt = False
            _clear(out_dir, "sheet_*.jpg")
    if raws and not burnt:
        _run(cmd(burn=False), timeout=3600)
    _clear(out_dir, "_raw_*.jpg")

    # Numeric order, as _extract_raw parses its index: ffmpeg's sheet_%03d
    # widens to four digits past 999, and a name sort would then put
    # sheet_1000.jpg between sheet_100.jpg and sheet_101.jpg — every tile from
    # the hundredth sheet on listed at the wrong second while the count below
    # still matched. 1000 sheets is 8.3 h at the defaults but 100 min at
    # --cols 3 --rows 2, which a long session recording reaches.
    sheets = sorted(out_dir.glob("sheet_*.jpg"), key=lambda p: int(p.stem[len("sheet_"):]))
    per_sheet = cols * rows
    if len(sheets) != -(-len(raws) // per_sheet):
        raise RuntimeError(f"ffmpeg wrote {len(sheets)} sheets for {len(raws)} frames at {per_sheet} per sheet")
    listing: list[dict[str, Any]] = []
    for i, sheet in enumerate(sheets):
        tiles = []
        for j in range(per_sheet):
            k = i * per_sheet + j
            if k >= len(raws):
                break
            sec = round(start + k * every_sec, 3)
            tiles.append({"row": j // cols, "col": j % cols, "sec": sec, "ts": _fmt_ts(sec)})
        listing.append({
            "file": sheet.name,
            "first_sec": tiles[0]["sec"] if tiles else None,
            "last_sec": tiles[-1]["sec"] if tiles else None,
            "tiles": tiles,
        })
    sidecar = {
        "video": str(video),
        "video_duration_sec": info.duration_sec,
        "start_sec": start,
        "end_sec": stop,
        "every_sec": every_sec,
        "cols": cols,
        "rows": rows,
        "tile_height": tile_height,
        "seconds_per_sheet": per_sheet * every_sec,
        "burnt_in_timestamps": burnt,
        "sheets": listing,
    }
    (out_dir / "sheets.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return sheets


# ── draft writer ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DraftSpan:
    """One in-play span as the labeller read it: serve contact to point end,
    in seconds, with how sure it was (0–1)."""
    start: float
    end: float
    confidence: float
    note: str = ""
    review: bool | None = None   # None → decided by confidence alone


def default_draft_path(test_id: str) -> Path:
    return DRAFTS_DIR / f"{test_id}_deadtime.json"


def _file_duration(duration_sec: float) -> float:
    """`video_duration_sec` as a draft stores it: ffprobe's value rounded *up*
    to the tenth of a second the spans are stored at.

    One resolution for the whole file is what keeps a draft inside the pinning
    suite's over-run check (test_labels_fit_inside_the_declared_duration:
    every parsed end <= video_duration_sec). Stored unrounded, a rally still
    running when the video ends — 628.0 read off a 627.96 s video — was
    accepted and then written as "10:28" beside 627.96, which that check
    classes as over-running and the harness clamps silently. Up rather than
    nearest so such a span always fits and the stored duration never
    understates the file.
    """
    tenth = round(duration_sec, 1)
    return tenth if tenth >= duration_sec else round(tenth + 0.1, 1)


def validate_spans(spans: list[DraftSpan], duration_sec: float) -> None:
    """Positive, sorted, disjoint, inside [0, duration] at the tenth of a
    second the file holds, confidence in [0, 1].

    Raises rather than reporting: a draft with a reversed or overlapping span
    would load (the harness loader is permissive) and score as a plausible
    number, which is exactly how a bad label hides.
    """
    if duration_sec <= 0:
        raise ValueError("video_duration_sec must be positive")
    if not spans:
        raise ValueError("a draft needs at least one span")
    prev_end = 0.0
    stored_duration = _file_duration(duration_sec)
    for i, s in enumerate(spans):
        if not (math.isfinite(s.start) and math.isfinite(s.end) and math.isfinite(s.confidence)):
            raise ValueError(f"span {i}: non-finite value ({s.start}, {s.end}, {s.confidence})")
        if s.start < 0 or s.end < 0:
            raise ValueError(f"span {i} ({s.start}s-{s.end}s): negative time")
        where = f"span {i} ({_fmt_ts(s.start)}-{_fmt_ts(s.end)})"
        if s.end <= s.start:
            raise ValueError(f"{where}: end is not after start")
        if round(s.end, 1) > stored_duration:
            # Both sides at the tenth the file holds — build_draft stores spans
            # to 0.1 s beside _file_duration(duration_sec) — so this is the
            # comparison the pinning suite makes on the written file, and a
            # rally still running when the video ends (627.96 s on test3) is
            # accepted rather than refused as 628.0 > 627.96.
            raise ValueError(
                f"{where}: ends past the video ({_fmt_ts(stored_duration)}, {duration_sec:.3f}s)"
            )
        if s.start < prev_end:
            raise ValueError(f"{where}: overlaps or precedes the previous span (ends {_fmt_ts(prev_end)})")
        if not 0.0 <= s.confidence <= 1.0:
            raise ValueError(f"{where}: confidence {s.confidence} is not in [0, 1]")
        prev_end = s.end


def build_draft(
    test_id: str,
    spans: list[DraftSpan],
    *,
    duration_sec: float,
    source_video_md5: str,
    source_frame_height: int,
    source_video_file: str,
    labeled_at: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """The draft fixture as a dict, in the dead-time format minus tiers.

    Tier-less on purpose: every span is in-play, as in test2–5, and the loader
    treats an untagged span as in-play. `labeler`/`labeling` are fixed, not
    parameters — see the module docstring.
    """
    # Round to what the file will hold (tenths of a second, confidence to 2 dp)
    # before validating and deciding `review`, so a 10.01-10.04 span cannot pass
    # as positive and land as "00:10"-"00:10", and a 0.699 is flagged exactly
    # as the stored 0.7 says it should be.
    spans = [replace(s, start=round(s.start, 1), end=round(s.end, 1), confidence=round(s.confidence, 2))
             for s in spans]
    validate_spans(spans, duration_sec)
    out_spans: list[dict[str, Any]] = []
    review_count = 0
    for s in spans:
        review = bool(s.review) or s.confidence < REVIEW_BELOW
        review_count += int(review)
        item: dict[str, Any] = {
            "start": _fmt_ts(s.start),
            "end": _fmt_ts(s.end),
            "confidence": s.confidence,
            "review": review,
        }
        if s.note:
            item["note"] = s.note
        out_spans.append(item)
    return {
        "test_id": test_id,
        "labeler": LABELER,
        "labeling": LABELING,
        "labeled_at": labeled_at or datetime.now(timezone.utc).date().isoformat(),
        "model": model,
        "source_video_file": source_video_file,
        "source_video_md5": source_video_md5,
        "video_duration_sec": _file_duration(duration_sec),
        "source_frame_height": source_frame_height,
        "review_below": REVIEW_BELOW,
        "review_count": review_count,
        "note": DRAFT_NOTE,
        "spans": out_spans,
    }


def write_draft(out_path: Path, draft: dict[str, Any]) -> Path:
    """Write a draft, refusing the two places it must never land: the fixtures
    directory itself, and on top of any file that is not already an AI draft."""
    if draft.get("labeling") != LABELING or draft.get("labeler") != LABELER:
        raise ValueError("refusing to write: not an AI draft (labeler/labeling have been changed)")
    resolved = out_path.resolve()
    if resolved.parent == FIXTURES_DIR.resolve():
        raise ValueError(
            f"refusing to write into {FIXTURES_DIR}: AI drafts go to {DRAFTS_DIR} until a person reviews them"
        )
    if resolved.exists():
        try:
            existing = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if not isinstance(existing, dict) or existing.get("labeling") != LABELING:
            raise ValueError(f"refusing to overwrite {out_path}: it is not an AI draft")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return resolved


def load_spans_file(path: Path) -> list[DraftSpan]:
    """The labeller's span list: a JSON list (or {"spans": [...]}) of
    {start, end, confidence, note?, review?}; start/end as mm:ss or seconds."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("spans") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f'{path}: expected a list of spans or {{"spans": [...]}}')
    spans: list[DraftSpan] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "start" not in item or "end" not in item:
            raise ValueError(f"{path}: span {i} needs start and end")
        if "confidence" not in item:
            raise ValueError(f"{path}: span {i} has no confidence — every draft span records one")
        spans.append(DraftSpan(
            start=_parse_ts(item["start"]),
            end=_parse_ts(item["end"]),
            confidence=float(item["confidence"]),
            note=str(item.get("note", "")),
            review=None if item.get("review") is None else bool(item["review"]),
        ))
    return spans


def _parse_ts(value: Any) -> float:
    """mm:ss / hh:mm:ss / seconds → seconds. Delegates to the harness's parser
    so a draft and a fixture read timestamps identically."""
    from ml.eval.harness import parse_timestamp
    return parse_timestamp(value)


def draft_from_video(
    test_id: str,
    video: Path,
    spans: list[DraftSpan],
    labeled_at: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Pin the draft to the file it was labelled from: duration and height from
    ffprobe, content MD5 so it matches the ball-cache key."""
    info = probe(video)
    return build_draft(
        test_id, spans,
        duration_sec=info.duration_sec,
        source_video_md5=content_md5(video),
        source_frame_height=info.height,
        source_video_file=video.name,
        labeled_at=labeled_at,
        model=model,
    )


# ── scoring against the human fixture ──────────────────────────────────────

def score_draft(draft_path: Path, test_id: str, audit_limit: int = 12) -> DeadTimeSignals:
    """Seconds-level agreement between a draft and the human fixture for
    `test_id`: the draft's spans stand in as the "model keep-windows", so
    `live_removed_sec` is play the human labelled that the draft missed and
    `missed_dead` is time the draft called in-play that the human did not.

    Prints the harness's own report; records nothing — a draft is not a run.
    Rally-level recall/precision and boundary error are CF-593's matcher.
    """
    from ml.eval import harness
    from ml.eval.metrics import evaluate_deadtime

    human = harness.load_deadtime_fixture(test_id)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    if draft.get("labeling") != LABELING:
        raise ValueError(f"{draft_path}: not an AI draft (labeling={draft.get('labeling')!r})")
    if draft.get("test_id") != test_id:
        print(
            f"WARNING: draft test_id {draft.get('test_id')!r} != {test_id!r} — "
            f"the draft was written for a different game; the numbers below are not an agreement measure"
        )
    draft_md5 = draft.get("source_video_md5")
    human_md5 = human.raw.get("source_video_md5")
    if draft_md5 and human_md5 and draft_md5 != human_md5:
        print(
            f"WARNING: draft md5 {draft_md5} != fixture md5 {human_md5} — "
            f"the draft was labelled off a different file; the numbers below are not an agreement measure"
        )
    keep = [(harness.parse_timestamp(s["start"]), harness.parse_timestamp(s["end"])) for s in draft["spans"]]
    signals = evaluate_deadtime(human.keep, keep, human.duration)
    print(f"== {test_id}: AI draft {draft_path.name} vs human fixture ({len(human.keep)} human spans, "
          f"{len(keep)} draft spans, {draft.get('review_count', '?')} flagged for review) ==")
    print(harness.format_deadtime(signals))
    print(harness.format_deadtime_audit(signals, limit=audit_limit, recorded=False))
    return signals


# ── CLI ────────────────────────────────────────────────────────────────────

def _default_out(video: Path, kind: str) -> Path:
    return FRAMES_DIR / video.stem / kind


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="duration, size and fps via ffprobe")
    s.add_argument("video", type=Path)

    s = sub.add_parser("md5", help="content MD5 (equals source_video_md5 / the ball-cache key)")
    s.add_argument("video", type=Path)

    s = sub.add_parser(
        "locate",
        help=f"find a fixture's video by md5 in ${FIXTURE_VIDEO_DIR_ENV}, "
             f"or an unlabelled game's by file name in ${EXTRA_VIDEO_DIR_ENV}",
    )
    s.add_argument("--test", required=True,
                   help="fixture id, e.g. test2 (reads only its source_video_md5), or a new id "
                        f"whose video is <id>.mp4 (any video suffix) in ${EXTRA_VIDEO_DIR_ENV}")
    s.add_argument("--dir", type=Path, default=None,
                   help="override the directory searched, whichever of the two the id selects")

    s = sub.add_parser("sheets", help="contact sheets: cols×rows tiles, one per --every seconds")
    s.add_argument("video", type=Path)
    s.add_argument("--out", type=Path, default=None, help="default ml/eval/frames/<video>/sheets")
    s.add_argument("--every", type=float, default=1.0)
    s.add_argument("--cols", type=int, default=6)
    s.add_argument("--rows", type=int, default=5)
    s.add_argument("--tile-height", type=int, default=180)
    s.add_argument("--start", type=float, default=None)
    s.add_argument("--end", type=float, default=None)

    s = sub.add_parser("frames", help="single frames every --every seconds, or dense ones --around a time")
    s.add_argument("video", type=Path)
    s.add_argument("--out", type=Path, default=None, help="default ml/eval/frames/<video>/{1fps,dense_<t>s}")
    s.add_argument("--every", type=float, default=None, help="default 1.0, or 0.5 with --around")
    s.add_argument("--start", type=float, default=None)
    s.add_argument("--end", type=float, default=None)
    s.add_argument("--around", type=float, default=None, help="centre of a dense window (seconds)")
    s.add_argument("--radius", type=float, default=4.0)
    s.add_argument("--height", type=int, default=360)

    s = sub.add_parser("write", help="validate a span list and write it as an ai-draft fixture")
    s.add_argument("--test", required=True, help="test_id for the draft (a new id for an unlabelled game)")
    s.add_argument("--video", type=Path, required=True, help="the exact file the frames came from")
    s.add_argument("--spans", type=Path, required=True, help="JSON list of {start, end, confidence, note?}")
    s.add_argument("--out", type=Path, default=None, help="default ml/eval/fixtures/drafts/<test>_deadtime.json")
    s.add_argument("--model", default=None, help="the model that read the frames, e.g. claude-fable-5-1")
    s.add_argument("--labeled-at", default=None, help="ISO date; default today (UTC)")

    s = sub.add_parser("score", help="seconds-level agreement of a draft with the human fixture")
    s.add_argument("--test", required=True)
    s.add_argument("--draft", type=Path, default=None, help="default ml/eval/fixtures/drafts/<test>_deadtime.json")
    s.add_argument("--audit-limit", type=int, default=12, help="0 prints every divergence")

    args = p.parse_args(argv)

    if args.cmd == "probe":
        info = probe(args.video)
        print(json.dumps({
            "duration_sec": info.duration_sec, "duration": _fmt_ts(info.duration_sec),
            "width": info.width, "height": info.height, "fps": round(info.fps, 3),
        }, indent=2))
    elif args.cmd == "md5":
        print(content_md5(args.video))
    elif args.cmd == "locate":
        fixture = FIXTURES_DIR / f"{args.test}_deadtime.json"
        if fixture.exists():
            md5 = json.loads(fixture.read_text(encoding="utf-8")).get("source_video_md5")
            if not md5:
                raise SystemExit(f"{fixture} has no source_video_md5")
            video_dir = args.dir or video_dir_from_env(FIXTURE_VIDEO_DIR_ENV)
            if not video_dir.is_dir():
                raise SystemExit(f"--dir {video_dir} is not a directory")
            found = find_video_by_md5(md5, video_dir)
            if found is None:
                raise SystemExit(f"no file in {video_dir} has md5 {md5} ({len(list_videos(video_dir))} videos hashed)")
        else:
            # An unlabelled game: nothing to match an md5 against, so the id
            # names the file. Say which directory was meant when neither is
            # given, since a mistyped fixture id lands here too.
            if args.dir is None and not os.environ.get(EXTRA_VIDEO_DIR_ENV):
                raise SystemExit(
                    f"no fixture {fixture}, and {EXTRA_VIDEO_DIR_ENV} is not set: a fixture id is found by "
                    f"md5 in ${FIXTURE_VIDEO_DIR_ENV}, an unlabelled game by its file name "
                    f"({args.test}.mp4) in ${EXTRA_VIDEO_DIR_ENV}"
                )
            video_dir = args.dir or video_dir_from_env(EXTRA_VIDEO_DIR_ENV)
            if not video_dir.is_dir():
                raise SystemExit(f"--dir {video_dir} is not a directory")
            try:
                found = find_video_by_name(args.test, video_dir)
            except ValueError as e:
                raise SystemExit(str(e)) from None
            if found is None:
                raise SystemExit(
                    f"no fixture {fixture} and no {args.test}.* among the {len(list_videos(video_dir))} "
                    f"videos in {video_dir}"
                )
        print(found)
    elif args.cmd == "sheets":
        out = args.out or _default_out(args.video, "sheets")
        sheets = contact_sheets(
            args.video, out, every_sec=args.every, cols=args.cols, rows=args.rows,
            tile_height=args.tile_height, start=args.start, end=args.end,
        )
        print(f"{len(sheets)} sheets -> {out} (sheets.json maps tile -> timestamp)")
    elif args.cmd == "frames":
        if args.around is not None:
            if args.start is not None or args.end is not None:
                p.error("--around sets its own range (--radius); it cannot be combined with --start/--end")
            out = args.out or _default_out(args.video, f"dense_{args.around:.0f}s")
            frames = dense_frames(
                args.video, args.around, out, radius=args.radius,
                every_sec=0.5 if args.every is None else args.every, height=args.height,
            )
        else:
            out = args.out or _default_out(args.video, "1fps")
            frames = extract_frames(
                args.video, out, every_sec=1.0 if args.every is None else args.every,
                start=args.start, end=args.end, height=args.height,
            )
        print(f"{len(frames)} frames -> {out} (frames.json maps file -> timestamp)")
    elif args.cmd == "write":
        spans = load_spans_file(args.spans)
        draft = draft_from_video(args.test, args.video, spans, labeled_at=args.labeled_at, model=args.model)
        path = write_draft(args.out or default_draft_path(args.test), draft)
        print(f"wrote {path}: {len(draft['spans'])} spans, {draft['review_count']} flagged for review "
              f"(confidence < {REVIEW_BELOW})")
    elif args.cmd == "score":
        score_draft(args.draft or default_draft_path(args.test), args.test, audit_limit=args.audit_limit)


if __name__ == "__main__":
    main()
