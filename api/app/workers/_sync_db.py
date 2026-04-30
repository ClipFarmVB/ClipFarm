"""Synchronous DB helpers for use inside Celery tasks (no asyncio event loop)."""
import uuid
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models.game import Game, GameStatus
from app.models.clip import Clip, ActionType
from app.models.dead_time import DeadTimeClip, DeadTimeRun, DeadTimeRunStatus

# Sync engine (Celery workers don't run in an asyncio loop)
_sync_url = settings.database_url.replace("+asyncpg", "")
_engine = create_engine(_sync_url, pool_pre_ping=True)


def sync_get_game(game_id: uuid.UUID) -> Game | None:
    with Session(_engine) as s:
        return s.get(Game, game_id)


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
        if processed_at:
            game.processed_at = processed_at
        if error_message:
            game.error_message = error_message
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
                start_time=row["start_time"],
                end_time=row["end_time"],
                clip_url=row["clip_url"],
                thumbnail_url=row.get("thumbnail_url"),
                labels=row.get("labels", []),
            )
            s.add(clip)
        s.commit()


def sync_set_dead_time_run_status(
    run_id: uuid.UUID,
    status: str,
    processed_at: datetime | None = None,
    error_message: str | None = None,
):
    with Session(_engine) as s:
        run = s.get(DeadTimeRun, run_id)
        if not run:
            return
        run.status = DeadTimeRunStatus(status)
        if processed_at:
            run.processed_at = processed_at
        if error_message:
            run.error_message = error_message
        s.commit()


def sync_save_dead_time_clips(rows: list[dict]):
    with Session(_engine) as s:
        for row in rows:
            clip = DeadTimeClip(
                id=row["id"],
                run_id=row["run_id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                score=row["score"],
                clip_url=row["clip_url"],
                thumbnail_url=row.get("thumbnail_url"),
            )
            s.add(clip)
        s.commit()


def sync_set_dead_time_run_status(
    run_id: uuid.UUID,
    status: str,
    processed_at: datetime | None = None,
    error_message: str | None = None,
):
    with Session(_engine) as s:
        run = s.get(DeadTimeRun, run_id)
        if not run:
            return
        run.status = DeadTimeRunStatus(status)
        if processed_at:
            run.processed_at = processed_at
        if error_message:
            run.error_message = error_message
        s.commit()


def sync_save_dead_time_clips(rows: list[dict]):
    with Session(_engine) as s:
        for row in rows:
            clip = DeadTimeClip(
                id=row["id"],
                run_id=row["run_id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                score=row["score"],
                clip_url=row["clip_url"],
                thumbnail_url=row.get("thumbnail_url"),
            )
            s.add(clip)
        s.commit()
