import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.config import settings
from app.database import get_db
from app.models.game import Game, GameStatus
from app.models.clip import Clip
from app.schemas.game import (
    GameOut,
    GameRename,
    UploadComplete,
    UploadConfig,
    UploadCreate,
    UploadPart,
    UploadTicket,
)
from app.services import storage
from app.workers.tasks import process_game_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/games", tags=["games"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


@router.get("", response_model=list[GameOut])
async def list_games(user_id: UserId, db: DB):
    # `uploading` rows are excluded: the browser is still PUTting the video to
    # R2 and there is nothing to show or open yet. A row only becomes a game in
    # the Library once the object is confirmed (see complete_upload).
    result = await db.execute(
        select(Game)
        .where(Game.owner_id == user_id, Game.status != GameStatus.uploading)
        .order_by(Game.created_at.desc())
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


# ── Presigned direct-to-R2 uploads (CF-163) ─────────────────────────────────
# The api issues an upload ticket and later confirms the object; the video
# itself goes browser → R2 and never passes through this process.
#
# NOTE: these static paths must stay ABOVE `GET /{game_id}` — FastAPI matches
# in declaration order, so "/games/upload-config" would otherwise be parsed as
# a game_id and 422 on the UUID conversion.


def _sanitize_filename(filename: str) -> str:
    """Strip path separators so a crafted filename can't escape the key prefix."""
    return (filename or "upload.mp4").replace("/", "_").replace("\\", "_").replace("..", "_")


async def _sweep_abandoned_uploads(user_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Drop this user's upload tickets that were never completed.

    A closed tab or a failed transfer leaves an `uploading` row and, for a
    multipart upload, parts that R2 bills for until they are aborted. Sweeping
    on the owner's next presign keeps that bounded without a scheduler — the
    only user who accumulates stale tickets is one who is actively uploading.

    Best-effort by construction: a failure here must never block a new upload.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.abandoned_upload_hours)
    try:
        result = await db.execute(
            select(Game).where(
                Game.owner_id == user_id,
                Game.status == GameStatus.uploading,
                Game.created_at < cutoff,
            )
        )
        stale = result.scalars().all()
        for game in stale:
            key = urlparse(game.raw_video_url or "").path.lstrip("/")
            # Both, not either. `upload_id` set does NOT imply the object
            # doesn't exist: complete_multipart runs before the row is claimed,
            # so a request that dies between assembly and the claim leaves a
            # real object on a row that still carries an upload id. Aborting
            # and then deleting covers that as well as the plain cases —
            # delete_object on a missing key is a no-op, and aborting an
            # already-completed upload just warns.
            if key and game.upload_id:
                try:
                    await run_in_threadpool(storage.abort_multipart, key, game.upload_id)
                except Exception:
                    logger.warning(
                        "Abort of abandoned multipart upload failed for game %s",
                        game.id, exc_info=True,
                    )
            if key:
                # This row is the only record of the key. Deleting it without
                # the object would strand the object unreferenced and billed.
                try:
                    await run_in_threadpool(storage.delete_file, key)
                except Exception:
                    logger.warning(
                        "Cleanup of abandoned upload object failed for game %s",
                        game.id, exc_info=True,
                    )
            await db.delete(game)
        if stale:
            await db.commit()
            logger.info("Swept %d abandoned upload(s) for user %s", len(stale), user_id)
    except Exception:
        logger.warning("Abandoned-upload sweep failed for user %s", user_id, exc_info=True)
        await db.rollback()


@router.get("/upload-config", response_model=UploadConfig)
async def upload_config(user_id: UserId) -> UploadConfig:
    """
    The limits the client must respect. Served rather than hardcoded in the web
    app so the advertised cap can never drift from the enforced one, and so
    CF-91 has one place to add remaining quota.
    """
    return UploadConfig(
        max_upload_bytes=settings.max_upload_bytes,
        allowed_content_types=sorted(settings.allowed_content_types_set),
        single_put_max_bytes=settings.single_put_max_bytes,
        part_size_bytes=settings.upload_part_size_bytes,
        url_ttl_seconds=settings.upload_url_ttl_seconds,
    )


@router.post("/uploads", response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
async def create_upload(body: UploadCreate, user_id: UserId, db: DB) -> UploadTicket:
    """
    Validate an intended upload and hand back presigned URLs for it.

    Both checks here happen before a single byte moves. The content type is
    signed into the URL, so R2 itself rejects a mismatched upload; the declared
    size is checked against the cap and re-verified against the real object in
    complete_upload, because a presigned PUT cannot enforce Content-Length.
    """
    content_type = body.content_type.split(";")[0].strip().lower()
    if content_type not in settings.allowed_content_types_set:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: {sorted(settings.allowed_content_types_set)}",
        )
    if body.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    await _sweep_abandoned_uploads(user_id, db)

    game_id = uuid.uuid4()
    key = storage.game_raw_key(game_id, _sanitize_filename(body.filename))
    ttl = settings.upload_url_ttl_seconds

    mode: Literal["single", "multipart"]
    upload_url: str | None = None
    upload_id: str | None = None
    part_size: int | None = None
    parts: list[UploadPart] = []

    try:
        if body.size_bytes <= settings.single_put_max_bytes:
            mode = "single"
            # Pure local signing, no network — safe to call on the event loop.
            upload_url = storage.presign_put(key, content_type, ttl)
        else:
            mode = "multipart"
            part_size = settings.upload_part_size_bytes
            upload_id = await run_in_threadpool(storage.create_multipart, key, content_type)
            parts = [
                UploadPart(
                    part_number=n,
                    url=storage.presign_upload_part(key, upload_id, n, ttl),
                )
                for n in range(1, storage.plan_part_count(body.size_bytes, part_size) + 1)
            ]
    except Exception:
        logger.exception("Could not presign upload for game %s", game_id)
        raise HTTPException(status_code=502, detail="Could not start upload")

    game = Game(
        id=game_id,
        owner_id=user_id,
        title=body.title,
        status=GameStatus.uploading,
        raw_video_url=f"{settings.r2_public_url}/{key}",
        condense_requested=body.condense,
        upload_id=upload_id,
    )
    try:
        db.add(game)
        await db.commit()
    except Exception:
        # The row is the only handle on the multipart upload — delete_game and
        # the sweep both find it through the row. If the insert fails, nothing
        # would ever be able to abort it, and its parts bill indefinitely.
        logger.exception("Could not record upload for game %s", game_id)
        await db.rollback()
        if upload_id:
            try:
                await run_in_threadpool(storage.abort_multipart, key, upload_id)
            except Exception:
                logger.warning(
                    "Abort of unrecorded multipart upload failed for %s", key, exc_info=True
                )
        raise HTTPException(status_code=500, detail="Could not start upload")

    return UploadTicket(
        game_id=game_id,
        mode=mode,
        content_type=content_type,
        expires_in=ttl,
        upload_url=upload_url,
        upload_id=upload_id,
        part_size_bytes=part_size,
        parts=parts,
    )


@router.post("/{game_id}/uploads/complete", response_model=GameOut)
async def complete_upload(
    game_id: uuid.UUID, body: UploadComplete, user_id: UserId, db: DB
) -> GameOut:
    """
    Confirm the object landed in R2, then queue processing.

    Enqueueing happens here rather than at presign time so a transfer that
    fails, is abandoned, or lies about its size never becomes a job — the
    worker only ever sees games whose video is known to exist.
    """
    game = await db.get(Game, game_id)
    if not game or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status != GameStatus.uploading:
        # Fast path for the obvious repeat. This check alone is NOT what makes
        # completion single-shot — see the conditional UPDATE below.
        raise HTTPException(status_code=409, detail="This upload is already complete")

    key = urlparse(game.raw_video_url or "").path.lstrip("/")
    if not key:
        raise HTTPException(status_code=500, detail="Upload is missing its storage key")

    if game.upload_id:
        if not body.parts:
            raise HTTPException(
                status_code=400, detail="A multipart upload must report its parts"
            )
        try:
            await run_in_threadpool(
                storage.complete_multipart,
                key,
                game.upload_id,
                [{"PartNumber": p.part_number, "ETag": p.etag} for p in body.parts],
            )
        except Exception:
            logger.exception("Multipart completion failed for game %s", game_id)
            raise HTTPException(
                status_code=400, detail="Could not assemble the uploaded parts"
            )

    head = await run_in_threadpool(storage.head_object, key)
    if head is None:
        raise HTTPException(status_code=400, detail="No uploaded video found for this game")

    if head["size"] > settings.max_upload_bytes:
        # The declared size passed the cap at presign but the real object did
        # not. Drop both the object and the row so nothing is left to process.
        logger.warning(
            "Upload for game %s exceeded the cap (%d bytes) — discarding",
            game_id, head["size"],
        )
        try:
            await run_in_threadpool(storage.delete_file, key)
        except Exception:
            logger.warning("Cleanup of oversize upload failed for %s", key, exc_info=True)
        await db.delete(game)
        await db.commit()
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    # Claim the upload atomically. The status check above is a read-then-write:
    # a HEAD (and, for multipart, an assembly call) sit between it and this
    # point, so two completion calls — a client retry on a slow response, a
    # double-click through a stalled request — can both pass it while the row
    # still reads `uploading`, and both enqueue. Making the transition itself
    # the guard means exactly one of them affects a row, and only that one
    # queues the pipeline.
    claimed = await db.execute(
        update(Game)
        .where(Game.id == game_id, Game.status == GameStatus.uploading)
        .values(status=GameStatus.queued, upload_id=None)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This upload is already complete")

    raw_url = game.raw_video_url
    condense = game.condense_requested
    await db.commit()
    await db.refresh(game)

    process_game_task.delay(str(game_id), raw_url, condense=condense)

    return GameOut.model_validate(game)


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
    if out.condensed_video_url:
        out.condensed_video_url = storage.presign_from_stored_url(
            out.condensed_video_url,
            download_filename=f"{game.title} (condensed).mp4",
        )
    return out


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
    for url in (game.raw_video_url, game.condensed_video_url):
        if url:
            r2_keys.append(urlparse(url).path.lstrip("/"))

    # A delete during an in-flight upload: abort the multipart so its uploaded
    # parts stop being billed. Parts are invisible to delete_object — only an
    # abort (or the bucket lifecycle rule) reclaims them.
    if game.upload_id and game.raw_video_url:
        raw_key = urlparse(game.raw_video_url).path.lstrip("/")
        try:
            await run_in_threadpool(storage.abort_multipart, raw_key, game.upload_id)
        except Exception:
            logger.warning(
                "Multipart abort failed for game %s", game_id, exc_info=True
            )

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
