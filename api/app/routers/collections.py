import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.game import Game
from app.models.collection import Collection, CollectionClip
from app.models.player import Player
from app.schemas.clip import ClipOut
from app.schemas.collection import CollectionOut, CollectionCreate, CollectionRename, CollectionAddClip
from app.services import storage

router = APIRouter(prefix="/collections", tags=["collections"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


async def _get_owned_collection(collection_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Collection:
    col = await db.get(Collection, collection_id)
    if not col or col.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Collection not found")
    return col


@router.get("", response_model=list[CollectionOut])
async def list_collections(user_id: UserId, db: DB):
    result = await db.execute(
        select(Collection)
        .where(Collection.owner_id == user_id)
        .order_by(Collection.created_at.desc())
    )
    collections = result.scalars().all()

    # Attach clip counts
    counts_q = await db.execute(
        select(CollectionClip.collection_id, func.count(CollectionClip.clip_id).label("n"))
        .where(CollectionClip.collection_id.in_([c.id for c in collections]))
        .group_by(CollectionClip.collection_id)
    )
    counts = {row.collection_id: row.n for row in counts_q}

    out = []
    for c in collections:
        d = CollectionOut.model_validate(c)
        d.clip_count = counts.get(c.id, 0)
        out.append(d)
    return out


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(body: CollectionCreate, user_id: UserId, db: DB):
    col = Collection(owner_id=user_id, name=body.name)
    db.add(col)
    await db.commit()
    await db.refresh(col)
    out = CollectionOut.model_validate(col)
    out.clip_count = 0
    return out


@router.patch("/{collection_id}", response_model=CollectionOut)
async def rename_collection(collection_id: uuid.UUID, body: CollectionRename, user_id: UserId, db: DB):
    col = await _get_owned_collection(collection_id, user_id, db)
    col.name = body.name
    await db.commit()
    await db.refresh(col)

    count_q = await db.execute(
        select(func.count(CollectionClip.clip_id)).where(CollectionClip.collection_id == col.id)
    )
    out = CollectionOut.model_validate(col)
    out.clip_count = count_q.scalar_one()
    return out


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(collection_id: uuid.UUID, user_id: UserId, db: DB):
    col = await _get_owned_collection(collection_id, user_id, db)
    await db.delete(col)
    await db.commit()


@router.get("/{collection_id}/clips", response_model=list[ClipOut])
async def list_collection_clips(collection_id: uuid.UUID, user_id: UserId, db: DB):
    await _get_owned_collection(collection_id, user_id, db)

    result = await db.execute(
        select(Clip)
        .join(CollectionClip, CollectionClip.clip_id == Clip.id)
        .where(CollectionClip.collection_id == collection_id)
        .order_by(CollectionClip.added_at.desc())
    )
    clips = result.scalars().all()

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
        if storage.r2_configured():
            d.clip_url = storage.presign_from_stored_url(c.clip_url, expires_in=3600)  # type: ignore[assignment]
            d.thumbnail_url = (
                storage.presign_from_stored_url(c.thumbnail_url, expires_in=3600)
                if c.thumbnail_url else None
            )
        else:
            d.clip_url = c.clip_url  # type: ignore[assignment]
            d.thumbnail_url = c.thumbnail_url
        out.append(d)
    return out


@router.post("/{collection_id}/clips", status_code=status.HTTP_201_CREATED)
async def add_clip_to_collection(
    collection_id: uuid.UUID, body: CollectionAddClip, user_id: UserId, db: DB
):
    col = await _get_owned_collection(collection_id, user_id, db)

    # Verify clip belongs to this user
    clip = await db.get(Clip, body.clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    game = await db.get(Game, clip.game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Upsert — silently succeed if already in collection
    existing = await db.execute(
        select(CollectionClip).where(
            CollectionClip.collection_id == col.id,
            CollectionClip.clip_id == body.clip_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(CollectionClip(collection_id=col.id, clip_id=body.clip_id))
        await db.commit()

    return {"collection_id": str(col.id), "clip_id": str(body.clip_id)}


@router.delete("/{collection_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_clip_from_collection(
    collection_id: uuid.UUID, clip_id: uuid.UUID, user_id: UserId, db: DB
):
    await _get_owned_collection(collection_id, user_id, db)

    result = await db.execute(
        select(CollectionClip).where(
            CollectionClip.collection_id == collection_id,
            CollectionClip.clip_id == clip_id,
        )
    )
    cc = result.scalar_one_or_none()
    if cc:
        await db.delete(cc)
        await db.commit()
