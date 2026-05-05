import logging
import uuid
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.config import settings
from app.database import get_db
from app.models.game import Game, GameStatus
from app.models.clip import Clip
from app.schemas.game import GameOut, GameRename
from app.services import storage
from app.workers.tasks import process_game_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/games", tags=["games"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


@router.get("", response_model=list[GameOut])
async def list_games(user_id: UserId, db: DB):
    result = await db.execute(
        select(Game).where(Game.owner_id == user_id).order_by(Game.created_at.desc())
    )
    games = result.scalars().all()

    # Attach clip counts
    clip_counts_q = await db.execute(
        select(Clip.game_id, func.count(Clip.id).label("n"))
        .where(Clip.game_id.in_([g.id for g in games]))
        .group_by(Clip.game_id)
    )
    counts = {row.game_id: row.n for row in clip_counts_q}

    out = []
    for g in games:
        d = GameOut.model_validate(g)
        d.clip_count = counts.get(g.id, 0)
        out.append(d)
    return out


@router.get("/{game_id}", response_model=GameOut)
async def get_game(game_id: uuid.UUID, user_id: UserId, db: DB):
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")
    clip_count_q = await db.execute(
        select(func.count(Clip.id)).where(Clip.game_id == game_id)
    )
    count = clip_count_q.scalar_one()
    out = GameOut.model_validate(game)
    out.clip_count = count
    return out


@router.post("", response_model=GameOut, status_code=status.HTTP_201_CREATED)
async def create_game(
    user_id: UserId,
    file: Annotated[UploadFile, File(description="Game video file")],
    title: Annotated[str, Form(max_length=255)],
    db: DB,
):
    # Validate content type
    content_type = (file.content_type or "").lower()
    if content_type not in settings.allowed_content_types_set:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: {sorted(settings.allowed_content_types_set)}",
        )

    # Reject early if Content-Length header is present and already too large
    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    # Sanitize filename — strip path separators
    safe_filename = (file.filename or "upload.mp4").replace("/", "_").replace("\\", "_").replace("..", "_")

    game_id = uuid.uuid4()
    key = storage.game_raw_key(game_id, safe_filename)

    try:
        # LimitedReader enforces the hard cap during streaming even when
        # Content-Length is absent (file.size is None)
        limited = storage.LimitedReader(file.file, settings.max_upload_bytes)
        raw_url = storage.upload_fileobj(limited, key, content_type=content_type)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception:
        logger.exception("Storage upload failed for game %s", game_id)
        raise HTTPException(status_code=500, detail="Storage upload failed")

    game = Game(
        id=game_id,
        owner_id=user_id,
        title=title,
        status=GameStatus.queued,
        raw_video_url=raw_url,
    )
    db.add(game)
    await db.commit()
    await db.refresh(game)

    # Enqueue processing job
    process_game_task.delay(str(game_id), raw_url)

    return GameOut.model_validate(game)


@router.patch("/{game_id}", response_model=GameOut)
async def rename_game(game_id: uuid.UUID, body: GameRename, user_id: UserId, db: DB):
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")
    game.title = body.title
    await db.commit()
    await db.refresh(game)
    return GameOut.model_validate(game)


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_game(game_id: uuid.UUID, user_id: UserId, db: DB):
    """Delete a game, its clips, and all associated R2 files."""
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")

    # Collect all R2 keys to delete (clips + thumbnails + raw video)
    clips_result = await db.execute(select(Clip).where(Clip.game_id == game_id))
    clips = clips_result.scalars().all()

    r2_keys: list[str] = []
    for clip in clips:
        for url in (clip.clip_url, clip.thumbnail_url):
            if url:
                r2_keys.append(urlparse(url).path.lstrip("/"))
    if game.raw_video_url:
        r2_keys.append(urlparse(game.raw_video_url).path.lstrip("/"))

    # Delete from DB (cascades to clips via relationship)
    await db.delete(game)
    await db.commit()

    # Best-effort R2 cleanup
    for key in r2_keys:
        try:
            if key:
                storage.delete_file(key)
        except Exception:
            logger.warning("R2 delete failed for key %s", key, exc_info=True)
