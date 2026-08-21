"""Synchronous DB helpers for use inside Celery tasks (no asyncio event loop)."""
import uuid
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models.game import Game, GameStatus
from app.models.clip import Clip, ActionType
from app.models.upload_event import UploadEvent

# Sync engine (Celery workers don't run in an asyncio loop)
_sync_url = settings.database_url.replace("+asyncpg", "")
_engine = create_engine(_sync_url, pool_pre_ping=True)


# games.error_message is String(1024). Nothing on the write path enforced that,
# and the value is `str(exc)` from an arbitrary failure — a Modal error carrying
# a remote traceback runs long. Overflowing raised DataError from *inside* the
# task's own `except` handler, which escaped past the retry decision and the
# `finally`, so the row never reached `failed` and sat in `processing` forever:
# the CF-184 stranded-game symptom, reached by the code that exists to explain a
# failure. Truncated here rather than at each call site so every writer is
# covered, including the next one.
_ERROR_MESSAGE_MAX = 1024


def _fit_error_message(message: str) -> str:
    """Clamp to the column width, marking the cut so it doesn't read as the end."""
    if len(message) <= _ERROR_MESSAGE_MAX:
        return message
    return message[:_ERROR_MESSAGE_MAX - 1] + "…"


def sync_get_game(game_id: uuid.UUID) -> Game | None:
    with Session(_engine) as s:
        return s.get(Game, game_id)


def sync_game_exists(game_id: uuid.UUID) -> bool:
    """True if the game row still exists.

    process_game uses this to abandon cleanly when a game is deleted mid-flight:
    the delete purges the game row and its raw video, so continuing would only
    download a missing file, violate the clips FK, and retry forever.
    """
    with Session(_engine) as s:
        return s.get(Game, game_id) is not None


def sync_set_game_status(
    game_id: uuid.UUID,
    status: str,
    processed_at: datetime | None = None,
    error_message: str | None = None,
):
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game:
            return
        game.status = GameStatus(status)
        # Keep the progress columns consistent with the coarse status: a
        # (re)started run begins at 0, a finished one reads 100%.
        if status == "processing":
            game.progress = 0.0
            game.progress_stage = None
            # A run starting clean clears whatever the last one left behind —
            # otherwise a note from a previous failure (see
            # sync_note_game_error) outlives it onto a game that goes `ready`.
            game.error_message = None
        elif status == "ready":
            game.progress = 1.0
            game.progress_stage = None
        if processed_at:
            game.processed_at = processed_at
        if error_message:
            game.error_message = _fit_error_message(error_message)
        s.commit()


def sync_note_game_error(game_id: uuid.UUID, message: str):
    """Record why a game is not progressing, without changing its status.

    For the case where the *environment* is broken rather than the game: the row
    stays where it is (`queued`, still runnable once the environment is fixed)
    but says why it stalled, so stranded games can be listed —

        SELECT id FROM games WHERE status = 'queued' AND error_message IS NOT NULL

    — rather than existing only as log lines. Cleared when a run next starts.
    """
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game:
            return
        game.error_message = _fit_error_message(message)
        s.commit()


def sync_set_game_progress(game_id: uuid.UUID, progress: float, stage: str | None):
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game:
            return
        game.progress = progress
        game.progress_stage = stage
        s.commit()


def sync_set_original_duration(game_id: uuid.UUID, original_duration: float):
    """Record the probed source duration.

    Set for every run, not just condensed ones. This column holds only measured
    values — the client's claim at upload time is kept out of it and lives in
    `upload_events.charged_seconds` instead, so `original_duration` continues to
    mean what its consumers assume.
    """
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game:
            return
        game.original_duration = original_duration
        s.commit()


def sync_settle_upload_charge(game_id: uuid.UUID, actual_seconds: float):
    """Correct this game's quota charge to the probed duration (CF-91).

    At accept time the api charges what the client declared, or the full
    per-video maximum when it declared nothing. This is the correction that
    makes the conservative default fair to honest clients — and that stops an
    under-declared duration from sticking as the charge.
    """
    with Session(_engine) as s:
        event = (
            s.query(UploadEvent).filter(UploadEvent.game_id == game_id).one_or_none()
        )
        if not event:
            return
        event.charged_seconds = max(0.0, actual_seconds)
        s.commit()


def sync_set_condensed_result(
    game_id: uuid.UUID,
    *,
    condensed_video_url: str,
    condensed_duration: float,
):
    """Record the condense stage's output.

    `original_duration` is deliberately not set here: it is written for every
    run as soon as the video is probed (see sync_set_original_duration), so
    re-writing the same measurement on the condense path only created a second
    place that had to agree.
    """
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game:
            return
        game.condensed_video_url = condensed_video_url
        game.condensed_duration = condensed_duration
        s.commit()


def sync_update_clip_url(
    clip_id: uuid.UUID,
    clip_url: str,
    thumbnail_url: str | None = None,
):
    with Session(_engine) as s:
        clip = s.get(Clip, clip_id)
        if not clip:
            return
        clip.clip_url = clip_url
        if thumbnail_url is not None:
            clip.thumbnail_url = thumbnail_url
        s.commit()


def sync_save_clips(rows: list[dict]):
    with Session(_engine) as s:
        for row in rows:
            clip = Clip(
                id=row["id"],
                game_id=row["game_id"],
                action_type=ActionType(row["action_type"]),
                confidence=row["confidence"],
                highlight_score=row.get("highlight_score"),
                start_time=row["start_time"],
                end_time=row["end_time"],
                clip_url=row["clip_url"],
                thumbnail_url=row.get("thumbnail_url"),
                labels=row.get("labels", []),
            )
            s.add(clip)
        s.commit()


def sync_delete_game_clips(game_id: uuid.UUID) -> list[str]:
    """
    Delete every Clip row for a game and return the R2 URLs (clip + thumbnail)
    that were referenced, so the caller can purge storage too.

    Makes process_game idempotent: a redelivered or re-enqueued task refreshes
    the game's clips instead of appending a duplicate set. Clip ids are fresh
    uuids each run, so old objects would otherwise orphan in R2. (CF-37)
    """
    urls: list[str] = []
    with Session(_engine) as s:
        clips = s.query(Clip).filter(Clip.game_id == game_id).all()
        for clip in clips:
            if clip.clip_url:
                urls.append(clip.clip_url)
            if clip.thumbnail_url:
                urls.append(clip.thumbnail_url)
            s.delete(clip)
        s.commit()
    return urls


def sync_expired_raw_uploads(cutoff: datetime) -> list[tuple[uuid.UUID, str]]:
    """
    (game_id, raw_video_url) for every game whose raw upload predates `cutoff`
    and is safe to purge (CF-194).

    Restricted to terminal states: a queued or processing game still needs its
    source, and the retention window is far longer than any run, so excluding
    them costs nothing.
    """
    with Session(_engine) as s:
        rows = (
            s.query(Game.id, Game.raw_video_url)
            .filter(
                Game.raw_video_url.isnot(None),
                Game.created_at < cutoff,
                Game.status.in_((GameStatus.ready, GameStatus.failed)),
            )
            .all()
        )
        return [(gid, url) for gid, url in rows]


def sync_referenced_raw_keys() -> set[str]:
    """
    Every R2 key still referenced by a game row, across all statuses.

    The safety guard for the retention sweep's reconciliation pass: an object
    in this set is live and must never be deleted, whatever its age. `uploading`
    rows are included deliberately — an upload in flight is referenced from the
    moment its ticket is issued (CF-163).
    """
    with Session(_engine) as s:
        rows = s.query(Game.raw_video_url).filter(Game.raw_video_url.isnot(None)).all()
    return {urlparse(url).path.lstrip("/") for (url,) in rows if url}


def sync_clear_raw_video_url(game_id: uuid.UUID, expected_url: str) -> bool:
    """
    Null a game's raw pointer, but only while it still holds `expected_url`.

    False means the row changed underneath us (deleted, or the pointer moved)
    and the caller must NOT delete the object — it may no longer be the one
    this sweep looked at.
    """
    with Session(_engine) as s:
        game = s.get(Game, game_id)
        if not game or game.raw_video_url != expected_url:
            return False
        game.raw_video_url = None
        s.commit()
        return True
