import logging
import uuid
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.clip import Clip, ActionType
from app.models.correction import Correction
from app.models.player import Player
from app.models.game import Game
from app.schemas.clip import (
    ClipDeleteRequest,
    ClipLabelsRequest,
    ClipOut,
    ClipTagRequest,
    ClipTrimRequest,
)
from app.services import storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clips"])

DB = Annotated[AsyncSession, Depends(get_db)]


async def _get_owned_clip(clip_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Clip:
    """Fetch a clip and verify the requesting user owns its parent game."""
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    game = await db.get(Game, clip.game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


def _rewrite_urls(clip: Clip) -> dict[str, str | None]:
    """Return public R2 URLs directly (bucket has public dev URL enabled).
    Fall back to presigned URLs if R2 credentials are configured."""
    if storage.r2_configured():
        return {
            "clip_url": storage.presign_from_stored_url(clip.clip_url, expires_in=3600),
            "thumbnail_url": (
                storage.presign_from_stored_url(clip.thumbnail_url, expires_in=3600)
                if clip.thumbnail_url
                else None
            ),
        }
    return {
        "clip_url": clip.clip_url,
        "thumbnail_url": clip.thumbnail_url,
    }


@router.get("/games/{game_id}/clips", response_model=list[ClipOut])
async def list_clips(
    game_id: uuid.UUID,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
    action_type: Annotated[str | None, Query()] = None,
    player_id: Annotated[uuid.UUID | None, Query()] = None,
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    # Verify game ownership
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")

    q = select(Clip).where(Clip.game_id == game_id)

    if action_type:
        types = [ActionType(t.strip()) for t in action_type.split(",") if t.strip()]
        if types:
            q = q.where(Clip.action_type.in_(types))

    if player_id:
        q = q.where(Clip.player_id == player_id)

    if min_confidence > 0:
        q = q.where(Clip.confidence >= min_confidence)

    q = q.order_by(Clip.start_time).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    clips = result.scalars().all()

    # Attach player names
    player_ids = {c.player_id for c in clips if c.player_id}
    player_map: dict[uuid.UUID, str] = {}
    if player_ids:
        pr = await db.execute(select(Player).where(Player.id.in_(player_ids)))
        for p in pr.scalars():
            player_map[p.id] = p.name

    out = []
    for c in clips:
        d = ClipOut.model_validate(c)
        d.player_name = player_map.get(c.player_id) if c.player_id else None  # type: ignore[arg-type]
        urls = _rewrite_urls(c)
        d.clip_url = urls["clip_url"]  # type: ignore[assignment]
        d.thumbnail_url = urls["thumbnail_url"]
        out.append(d)
    return out


@router.patch("/clips/{clip_id}/tag", response_model=ClipOut)
async def tag_clip(
    clip_id: uuid.UUID,
    body: ClipTagRequest,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    clip = await _get_owned_clip(clip_id, user_id, db)

    player = await db.get(Player, body.player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    clip.player_id = body.player_id
    await db.commit()
    await db.refresh(clip)

    out = ClipOut.model_validate(clip)
    out.player_name = player.name
    return out


VALID_LABELS = {"spike", "serve", "dig", "set", "block", "not_an_action"}


@router.patch("/clips/{clip_id}/labels", response_model=ClipOut)
async def update_clip_labels(
    clip_id: uuid.UUID,
    body: ClipLabelsRequest,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Set up to 2 action labels on a clip. Saves correction as ML training data."""
    invalid = set(body.labels) - VALID_LABELS
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid labels: {invalid}. Must be from: {VALID_LABELS}")

    clean_labels = [lb for lb in body.labels if lb != "not_an_action"]
    if len(clean_labels) > 2:
        raise HTTPException(status_code=400, detail="Maximum 2 action labels per clip")

    clip = await _get_owned_clip(clip_id, user_id, db)

    # Determine labels for correction record (preserve user selection order)
    user_labels = body.labels
    label_1 = user_labels[0] if len(user_labels) > 0 else "not_an_action"
    label_2 = user_labels[1] if len(user_labels) > 1 else None

    # Upsert correction — one row per clip per user
    existing = (await db.execute(
        select(Correction).where(
            Correction.clip_id == clip.id,
            Correction.user_id == user_id,
        )
    )).scalar_one_or_none()

    if existing:
        existing.corrected_label_1 = label_1
        existing.corrected_label_2 = label_2
    else:
        correction = Correction(
            clip_id=clip.id,
            user_id=user_id,
            original_action=clip.action_type,
            corrected_label_1=label_1,
            corrected_label_2=label_2,
            original_confidence=clip.confidence,
            start_time=clip.start_time,
            end_time=clip.end_time,
        )
        db.add(correction)

    # Update labels on clip (keep "not_an_action" so frontend knows it was explicit)
    if "not_an_action" in body.labels:
        clip.labels = ["not_an_action"]
    else:
        clip.labels = list(set(clean_labels))

    # Update primary action type based on labels
    if "not_an_action" in body.labels or not clean_labels:
        clip.action_type = ActionType.unknown
        clip.confidence = 0.0
    else:
        clip.action_type = ActionType(clean_labels[0])
        clip.confidence = 1.0

    await db.commit()
    await db.refresh(clip)

    out = ClipOut.model_validate(clip)
    urls = _rewrite_urls(clip)
    out.clip_url = urls["clip_url"]  # type: ignore[assignment]
    out.thumbnail_url = urls["thumbnail_url"]
    return out


TRIM_STEP = 2.0  # seconds
MAX_CLIP_DURATION = 30.0
MIN_CLIP_DURATION = 1.0


@router.patch("/clips/{clip_id}/trim", response_model=ClipOut)
async def trim_clip(
    clip_id: uuid.UUID,
    body: ClipTrimRequest,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Adjust a clip's start/end time and re-cut the video from the source.

    start_delta: negative = extend earlier, positive = shrink from start
    end_delta:   positive = extend later, negative = shrink from end
    """
    clip = await _get_owned_clip(clip_id, user_id, db)

    game = await db.get(Game, clip.game_id)
    if not game or not game.raw_video_url:
        raise HTTPException(status_code=400, detail="Source video not available for trimming")

    new_start = max(0, clip.start_time + body.start_delta)
    new_end = clip.end_time + body.end_delta
    if new_end <= new_start:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    duration = new_end - new_start
    if duration < MIN_CLIP_DURATION:
        raise HTTPException(status_code=400, detail=f"Clip too short (min {MIN_CLIP_DURATION}s)")
    if duration > MAX_CLIP_DURATION:
        raise HTTPException(status_code=400, detail=f"Clip too long (max {MAX_CLIP_DURATION}s)")

    # Update times in DB
    clip.start_time = new_start
    clip.end_time = new_end
    await db.commit()
    await db.refresh(clip)

    # Kick off background re-cut via Celery
    celery_app.send_task(
        "recut_clip",
        args=[str(clip.id), str(clip.game_id), game.raw_video_url, new_start, new_end],
    )

    out = ClipOut.model_validate(clip)
    urls = _rewrite_urls(clip)
    out.clip_url = urls["clip_url"]  # type: ignore[assignment]
    out.thumbnail_url = urls["thumbnail_url"]
    return out


@router.post("/clips/delete")
async def delete_clips(
    body: ClipDeleteRequest,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Bulk-delete clips. Verifies ownership of every ID before touching anything."""
    if not body.clip_ids:
        return {"deleted": 0}

    # Fetch all requested clips and their owning games in one go
    rows = (await db.execute(
        select(Clip, Game)
        .join(Game, Game.id == Clip.game_id)
        .where(Clip.id.in_(body.clip_ids))
    )).all()

    # Reject the entire request if any clip is missing or not owned
    if len(rows) != len(set(body.clip_ids)):
        raise HTTPException(status_code=404, detail="One or more clips not found")
    for _, game in rows:
        if game.owner_id != user_id:
            raise HTTPException(status_code=404, detail="One or more clips not found")

    # Delete from R2 (best-effort) and DB
    deleted = 0
    for clip, _ in rows:
        for url in (clip.clip_url, clip.thumbnail_url):
            if not url:
                continue
            try:
                key = urlparse(url).path.lstrip("/")
                if key:
                    storage.delete_file(key)
            except Exception:
                logger.warning("R2 delete failed for clip %s", clip.id, exc_info=True)
        await db.delete(clip)
        deleted += 1

    await db.commit()
    return {"deleted": deleted}


@router.get("/clips/{clip_id}/share")
async def share_clip(
    clip_id: uuid.UUID,
    db: DB,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    clip = await _get_owned_clip(clip_id, user_id, db)
    return {"url": storage.presign_from_stored_url(clip.clip_url, expires_in=3600)}
